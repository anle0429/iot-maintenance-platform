// Run a new, source-bound five-stage KFP training pipeline after tests and automatic deployment.
pipeline {
  agent any
  options { timeout(time: 80, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins' }
  stages {
    stage('Prepare sealed source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Invalid source commit') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit real VM session') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py admission' } }
    }
    stage('Test lineage and holdout gates') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py test' } }
    }
    stage('Build verify import images') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py build' } }
    }
    stage('Auto-deploy sealed KFP package') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py deploy' } }
    }
    stage('Fresh KFP data model readback') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py verify' } }
    }
  }
  post {
    unsuccessful { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-training.py cleanup' } }
    always { archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-training/**", allowEmptyArchive: true, fingerprint: true }
  }
}
