#!/usr/bin/python
'''
Testing:
1. Create an MSSQL RDS instance in account #1.
2. Run the script. It should error with 'no manual snapshots.'
3. Create a manual snapshot.
4. Run the script. It should share the snapshot and copy it over.
2. Run this script with from_account=mine, to_account=reference

1. Create a new dev stack
2. Update the Jenkins pipeline account numbers
3. Create a Jenkins pipeline and run the job

TODO: in the Jenkins pipeline, export the copied snapshot as an environment variable to use it in the next stage.
'''

import boto3
import os
import re
import sys
import time

def main():
    from_account = os.environ['FROM_ACCOUNT']
    from_region = os.environ['FROM_REGION']
    to_account = os.environ['TO_ACCOUNT']
    to_region = os.environ['TO_REGION']
    stack_name = os.environ['STACK_NAME']

    '''
    from_account = sys.argv[1]
    from_region = sys.argv[2]
    to_account = sys.argv[3]
    to_region = sys.argv[4]
    '''
    file_name = 'copied-rds-snapshot-name'
    from_client = get_rds_client(from_account, from_region)

    if from_account == to_account:
        to_client = from_client
    else:
        to_client = get_rds_client(to_account, to_region)

    engine = 'sqlserver-web'
    environment_tag = 'prod'
    name_tag = 'prod-rds'

    print("Looking for an MSSQL RDS instance with: engine=%s, environment_tag=%s, name_tag=%s..." % (engine, environment_tag, name_tag))
    instance_identifier = get_db_instance_identifier(from_client, engine, environment_tag, name_tag)
    if not instance_identifier:
        print("ERROR: no RDS instances were found with the specified tags.")
        exit(1)

    print ("Found production MSSQL RDS instance '%s'." % (instance_identifier))

    # TODO: check if the snapshot is automated. if it is, complain it can't be shared with the account.
    snapshots = from_client.describe_db_snapshots(DBInstanceIdentifier=instance_identifier, SnapshotType='manual')['DBSnapshots']

    # Get the most recent manual snapshot for this instance and share it with the target account.
    manual_snapshots = filter(lambda x: x['Status'] == 'available', snapshots)
    if not manual_snapshots:
        print("ERROR: there are no manual snapshots for this RDS instance.")
        exit(1)

    latest_snapshot = sorted(manual_snapshots, key=lambda x: x['SnapshotCreateTime'], reverse=True)[0]
    latest_snapshot_identifier = latest_snapshot['DBSnapshotIdentifier']
    print ("Latest manual snapshot for RDS instance '%s' is '%s'." % (instance_identifier, latest_snapshot_identifier))

    # Delete any older snapshots made by this job for this DB instance.
    # Keep the latest one if it's identical to the newest.
    # The naming convention for copied snapshots is:
    #   <original snapshot name>-<original datetime>-copied-from-<source account id>

    to_copy = False

    # If the user creates a new snapshot from an instance in the source account,
    # any older snapshots for this instance will be deleted in the destination account.
    existing_destination_snapshots = to_client.describe_db_snapshots(SnapshotType='manual')['DBSnapshots']

    if not existing_destination_snapshots:
        print("No existing snapshots exist for this RDS instance in the destination account.")
        to_copy = True
    else:
        for existing_snapshot in existing_destination_snapshots:
            pattern = '.*-copied-from-' + from_account
            if re.match(pattern, existing_snapshot['DBSnapshotIdentifier']):
                if existing_snapshot['DBInstanceIdentifier'] == instance_identifier:
                    # If it's an old backup, delete it and copy over the new one. Otherwise, do nothing.
                    if is_old_backup(to_client, latest_snapshot, existing_snapshot):
                        print("Found old backup '%s' for RDS instance '%s'. Deleting..." % (existing_snapshot['DBSnapshotIdentifier'], instance_identifier))
                        #to_client.delete_db_snapshot(DBSnapshotIdentifier=existing_snapshot['DBSnapshotIdentifier'])
                        to_copy = True

    new_snapshot_identifier = latest_snapshot_identifier + '-copied-from-' + from_account
    if to_copy:
        # Share the snapshot.
        from_client.modify_db_snapshot_attribute(DBSnapshotIdentifier=latest_snapshot_identifier, AttributeName='restore', ValuesToAdd=[to_account])
        print ("Shared RDS snapshot '%s' with account ID %s." % (latest_snapshot_identifier, to_account))

        response = to_client.copy_db_snapshot(SourceDBSnapshotIdentifier=latest_snapshot['DBSnapshotArn'], TargetDBSnapshotIdentifier=new_snapshot_identifier)['DBSnapshot']
        new_snapshot_arn = response['DBSnapshotArn']
        print("Copied RDS snapshot '%s' as '%s' to account %s." % (latest_snapshot_identifier, new_snapshot_identifier, to_account))

        while response['Status'] != 'available':
            print("Waiting for snapshot to become available...")
            time.sleep(5)
            response = to_client.describe_db_snapshots(DBSnapshotIdentifier=new_snapshot_identifier)['DBSnapshots'][0]

        # Add the source's snapshot createtime as a tag.
        tags = {'Key': 'SourceSnapshotCreateTime', 'Value': str(latest_snapshot['SnapshotCreateTime'])}
        to_client.add_tags_to_resource(ResourceName=new_snapshot_arn, Tags=[tags])
        print ("Added tags '%s:%s' to new snapshot." % (tags['Key'], tags['Value']))
    else:
        print("RDS snapshot '%s' is the most recent snapshot for this instance. Nothing to do." % (new_snapshot_identifier))

    # Write the snapshot name to a file (e.g. for it to be used a Jenkinsfile).
    with open(file_name, 'w') as fh:
        fh.write(new_snapshot_identifier)

def is_old_backup(client, last_snapshot_source, last_snapshot_destination):
    """Compare the destination's most recent backup name with the source's.

    The source backup might be brand new, but with the same name and ARN.
    Therefore, the only way to tell if it's new is to check the snapshot's
    create datetime.

    Params:
        client: the RDS boto3 client for the destination account
        last_snapshot_source: the source snapshot object
        last_snapshot_destination: the destination snapshot object
    """
    source_create_time = last_snapshot_source['SnapshotCreateTime']
    destination_arn = last_snapshot_destination['DBSnapshotArn']
    tag_list = client.list_tags_for_resource(ResourceName=destination_arn)['TagList']

    if not tag_list:
        return False

    tags = extract_keys_and_values(tag_list)

    return tags['SourceSnapshotCreateTime'] != str(source_create_time)

def get_db_instance_identifier(client, engine, environment_tag, name_tag):
    """Query all the RDS instances and filter them based on a tag.
    """
    instances = client.describe_db_instances()['DBInstances']

    if instances:
        mssql_instances = filter(lambda x: x['Engine'] == engine, instances)

        for instance in mssql_instances:
            arn = instance['DBInstanceArn']
            tag_list = client.list_tags_for_resource(ResourceName=arn)['TagList']

            if not tag_list:
                continue

            tags = extract_keys_and_values(tag_list)
            if 'Environment' in tags and 'Name' in tags:
                if tags['Environment'] == environment_tag and tags['Name'] == name_tag:
                    return instance['DBInstanceIdentifier']

    return None


def extract_keys_and_values(lst):
    """Convert a list of dicts like:
        [{'Key':'Name', 'Value':'Apple'}]
    into a single dict:
        {'Name':'Apple'}
    """
    new_dict = {}

    for item in lst:
        key = item['Key']
        value = item['Value']
        new_dict[key] = value

    return new_dict


def get_rds_client(account, region):
    role_name = 'ciinabox'
    role_session_name = 'copy-rds-snapshot'
    role_arn = 'arn:aws:iam::%s:role/%s' % (account, role_name)

    sts = boto3.client('sts', region_name=region)
    response = sts.assume_role(RoleArn=role_arn, RoleSessionName=role_session_name, DurationSeconds=900)
    client = boto3.client(
        'rds',
        region_name=region,
        aws_access_key_id=response['Credentials']['AccessKeyId'],
        aws_secret_access_key=response['Credentials']['SecretAccessKey'],
        aws_session_token=response['Credentials']['SessionToken']
    )

    return client


main()

