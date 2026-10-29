// Three separate private jobs share this reviewed release procedure and keep separate source-bound receipts.
pipeline {
  agent any
  options { timeout(time: 45, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins'; IOT_MODEL_VERSION = '6' }
  stages {
    stage('Prepare declared source and role') {
      steps {
        script {
          if (!(env.JOB_BASE_NAME in ['iot-llm-retrieval','iot-llm-drift','iot-llm-coordinator'])) { error('Use a declared private agent job') }
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the full source commit') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual source and VM session') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py admission' } }
    }
    stage('Test contracts API coverage and bound mutation cache') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py test' } }
    }
    stage('Build and inspect application and agent charts') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py build' } }
    }
    stage('Auto deploy MCP and accepted agent policy') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py deploy' } }
    }
    stage('Verify real tool calls and complete agent answers') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py verify' } }
    }
  }
  post {
    unsuccessful {
      script {
        if (fileExists("build-${env.BUILD_NUMBER}/scripts/ci-agents.py")) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py rollback' }
        }
      }
    }
    cleanup {
      script {
        if (fileExists("build-${env.BUILD_NUMBER}/scripts/ci-agents.py")) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents.py archive' }
        }
      }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-agents/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
