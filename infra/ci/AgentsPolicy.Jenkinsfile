// Retain the original three role jobs; admit complete model/tool/sandbox capabilities before any deployment.
pipeline {
  agent any
  options { timeout(time: 45, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment { IOT_CI_PROVIDER = 'jenkins'; IOT_MODEL_VERSION = '6' }
  parameters {
    string(name: 'SOURCE_COMMIT', defaultValue: '', description: 'Full source revision already sealed on the private lab')
    choice(name: 'ADMISSION_SCENARIO', choices: ['production', 'unsafe-tool'], description: 'The second choice changes only a synthetic candidate and must fail before deployment')
  }
  stages {
    stage('Prepare declared source and role') {
      steps {
        script {
          if (!(env.JOB_BASE_NAME in ['iot-llm-retrieval','iot-llm-drift','iot-llm-coordinator'])) { error('Use a declared private agent job') }
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the full source commit') }
          if (!(params.ADMISSION_SCENARIO in ['production','unsafe-tool'])) { error('Use a declared admission scenario') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.IOT_AGENT_POLICY_SCENARIO = params.ADMISSION_SCENARIO
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
        '''
      }
    }
    stage('Admit actual source and VM session') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py admission' } }
    }
    stage('Test contracts API coverage and bound mutation cache') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py test' } }
    }
    stage('Build and inspect application and agent charts') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py build' } }
    }
    stage('Admit model tools and sandbox with OPA') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py policy' } }
    }
    stage('Auto deploy MCP and accepted agent policy') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py deploy' } }
    }
    stage('Verify real tool calls and complete agent answers') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py verify' } }
    }
  }
  post {
    unsuccessful {
      script {
        if (fileExists("build-${env.BUILD_NUMBER}/scripts/ci-agents-policy.py")) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py rollback' }
        }
      }
    }
    cleanup {
      script {
        if (fileExists("build-${env.BUILD_NUMBER}/scripts/ci-agents-policy.py")) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-agents-policy.py archive' }
        }
      }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-agents/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
