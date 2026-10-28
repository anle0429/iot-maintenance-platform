// Pull production metadata, test/build and auto-deploy a separate private drift event receiver.
pipeline {
  agent any
  options { timeout(time: 85, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins' }
  stages {
    stage('Prepare sealed Eventing source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the full source commit') }
          if (!(params.MODEL_VERSION ==~ /[1-9][0-9]*/)) { error('Pin the production model version') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.IOT_MODEL_VERSION = params.MODEL_VERSION
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual VM session') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py admission' } } }
    stage('Pull production model and fixed training reference') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py select' } } }
    stage('Fresh regression coverage mutation') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py test' } } }
    stage('Build probe and import event image') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py build' } } }
    stage('Auto deploy Broker Trigger and KServe') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py deploy' } } }
    stage('Native filtered delivery persistent replay conflict') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py verify' } } }
  }
  post {
    cleanup {
      dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-drift-eventing.py archive' }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-drift-eventing/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
