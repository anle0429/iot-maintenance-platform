// Two independent jobs use the same audited pipeline: iot-stream-offline and iot-stream-online.
pipeline {
  agent any
  options { timeout(time: 30, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins'; IOT_MODEL_VERSION = '6' }
  stages {
    stage('Prepare sealed stream-store source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the source commit') }
          if (!(env.JOB_BASE_NAME in ['iot-stream-offline','iot-stream-online'])) { error('Use the declared separate stream jobs') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.IOT_STREAM_DESTINATION = env.JOB_BASE_NAME == 'iot-stream-offline' ? 'offline' : 'online'
          env.IOT_OFFLINE_RELEASE = params.OFFLINE_RELEASE ?: ''
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual VM session') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py admission' } } }
    stage('Test contracts native faults and replay') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py test' } } }
    stage('Build and probe native Feast job image') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py build' } } }
    stage('Auto deploy separate Airflow job') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py deploy' } } }
    stage('Actual job replay and independent store readback') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'iot-airflow', usernameVariable: 'IOT_AIRFLOW_USER', passwordVariable: 'IOT_AIRFLOW_PASSWORD')]) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py verify' }
        }
      }
    }
  }
  post {
    cleanup {
      dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-stream-push.py archive' }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-stream-push/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
