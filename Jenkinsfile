pipeline {
    agent { label "python" }

    triggers { pollSCM '*/1 * * * *' }

    stages {
        stage('Install Dependencies') {
            steps {
                sh 'make install-deps'
            }
        }

        stage('Format Check') {
            steps {
                sh 'uv run ruff format --check'
            }
        }

        stage('Lint') {
            steps {
                sh 'make lint'
            }
        }

        stage('Type Check') {
            steps {
                sh 'make type-check'
            }
        }

        stage('Unit Tests') {
            steps {
                sh 'make test'
            }
        }

        stage('Create Docker Image') {
            steps {
                sh 'make build'
            }
        }

        stage('Deploy Docker Image') {
            steps {
                sh 'make up-detach'
            }
        }
    }
}
