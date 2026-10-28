// Test, build and deploy instrumentation for the already active production model; retain model-version CI separately.
pipeline {
  agent any
  options { timeout(time: 85, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins' }
  stages {
    stage('Prepare sealed telemetry source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the full source commit') }
          if (!(params.MODEL_VERSION ==~ /[1-9][0-9]*/)) { error('Pin the current production model version') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.IOT_MODEL_VERSION = params.MODEL_VERSION
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual VM session') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py admission' } } }
    stage('Pull production model and bind current APIs') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py select' } } }
    stage('Fresh coverage mutation and contracts') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py test' } } }
    stage('Build probe import three images') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py build' } } }
    stage('Deploy private telemetry') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py deploy' } } }
    stage('Native images metrics and200machine predictions') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py verify' } } }
  }
  post {
    unsuccessful { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py rollback' } }
    cleanup {
      dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-observability.py archive' }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-observability/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
