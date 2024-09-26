pipeline {
    agent { label "default" }

    triggers { pollSCM '*/5 * * * *' }

    stages {
        stage('Install Depedencies') {
            steps {
                echo "install dependencies"
                sh 'sleep 10s'
            }
        }

        stage('Create Docker Image') {
            steps {
                echo "create docker image"
                sh 'sleep 10s'
            }
        }

        stage('Deploy Docker Image') {
            steps {
                echo "deploy docker image"
                sh 'sleep 10s'
            }
        }
    }
}