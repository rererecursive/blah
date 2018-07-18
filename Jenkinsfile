@Library('ciinabox') _

/*
  - Use the AWS CLI to find the latest snapshot
  - Export this as an environment variable
  - Pass it in as a parameter to the Python script
  - If the Python script passes, use the env variable in the stack update.
*/

pipeline {
  environment {
    REGION = 'ap-southeast-2'
    CIINABOX_ROLE = 'ciinabox'
  }

  agent {

  }

  stages {
    stage('Copy RDS snapshot') {
      steps {
        script {
          // Get the AWS account number
          def accounts = ['prod': '400480216381', 'prod': '537712071186']
          ENV.FROM_ACCOUNT = accounts[ENV.FROM_ACCOUNT]
          ENV.TO_ACCOUNT = accounts[ENV.TO_ACCOUNT]

        }
        println("Installing boto3 if it is not present...")
        sh 'if [[ $(pip show boto3) == "" ]]; then pip install boto3; fi'

        sh 'python copy-db-snapshot.py ${env.FROM_ACCOUNT} ${env.FROM_REGION} ${env.TO_ACCOUNT} ${env.TO_REGION}'

        script {
          // The name of the snapshot written by the Python script.
          snapshot = readFile('copied-rds-snapshot-name').trim()
        }

      }
    }
    stage('Update stack') {
      steps {
        // Update the stack while keeping all of the same parameters except for the snapshot ID
        println("Updating the CloudFormation stack with the new snapshot '${snapshot}'.")
        /*
        cloudformation(
          stackName: env.STACK_NAME,
          action: 'update',
          region: env.TO_REGION,
          accountId: env.TO_ACCOUNT,
          parameters: ['RDSSnapshotID': $snapshot]
          role: env.CIINABOX_ROLE
        )
        */
        echo $snapshot
      }
    }
  }
}
