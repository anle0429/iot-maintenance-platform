// Test/build/auto-deploy one production model with an isolated KServe endpoint and a paired feature API switch.
pipeline {
  agent any
  options { timeout(time: 85, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins' }
  stages {
    stage('Prepare sealed source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the full source commit') }
          if (!(params.MODEL_VERSION ==~ /[1-9][0-9]*/)) { error('Pin the production model version') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.IOT_MODEL_VERSION = params.MODEL_VERSION
          def parent = params.API_GATE_BUILD ?: ''
          if (!(parent ==~ /[1-9][0-9]{0,9}|/)) { error('Use an earlier build number or empty fresh-mutation mode') }
          env.IOT_API_GATE_PARENT = parent
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual VM session') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py admission' } } }
    stage('Pull production bundle and pin') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py select' } } }
    stage('API coverage and mutation gates') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py test' } } }
    stage('Build inspect import images') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py build' } } }
    stage('Stage private model and fresh features') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py stage' } } }
    stage('Two-model traffic and monitoring') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py ab' } } }
    stage('Atomic feature API switch') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py switch' } } }
    stage('Native pods and all-machine HTTP') { steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py verify' } } }
  }
  post {
    unsuccessful { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py cleanup' } }
    cleanup {
      script {
        try { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py restore-scaling' } }
        finally {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-serving.py archive' }
          archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-serving/**", allowEmptyArchive: true, fingerprint: true
        }
      }
    }
  }
}
