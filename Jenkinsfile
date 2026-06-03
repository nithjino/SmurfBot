pipeline {
    agent { label "python" }

    triggers { pollSCM '*/1 * * * *' }

    stages {
        stage('Install Dependencies') {
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