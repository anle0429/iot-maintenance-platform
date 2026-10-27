// Test, build and auto-deploy an isolated native Feast materialization DAG.
pipeline {
  agent any
  options { timeout(time: 20, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins' }
  stages {
    stage('Prepare sealed source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Invalid sealed commit') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Test contracts') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-materialization.py test' } }
    }
    stage('Build verified SDK images') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-materialization.py build' } }
    }
    stage('Auto-deploy Airflow pipeline') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-materialization.py deploy' } }
    }
    stage('Initial incremental replay readback') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'iot-airflow', usernameVariable: 'IOT_AIRFLOW_USER', passwordVariable: 'IOT_AIRFLOW_PASSWORD')]) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-materialization.py verify' }
        }
      }
    }
  }
  post {
    unsuccessful { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-materialization.py cleanup' } }
    always { archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-materialization/**", allowEmptyArchive: true, fingerprint: true }
  }
}
