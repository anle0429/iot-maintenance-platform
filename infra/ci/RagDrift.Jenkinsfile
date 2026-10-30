// Publish a source-sealed RAG release only after its actual packaged corpus passes document-distance review.
pipeline {
  agent any
  options { timeout(time: 45, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment {
    IOT_CI_PROVIDER = 'jenkins'
    IOT_MODEL_VERSION = '6'
    IOT_RAG_DRIFT_SCENARIO = "${params.CORPUS_SCENARIO}"
  }
  stages {
    stage('Prepare sealed RAG source') {
      steps {
        script {
          if (env.JOB_BASE_NAME != 'iot-llm-rag') { error('Use the declared RAG job') }
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
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py admission' } }
    }
    stage('Test corpus release guards and CI contracts') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py test' } }
    }
    stage('Build and inspect the RAG application image') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py build' } }
    }
    stage('Review actual corpus embedding distance') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py corpus-distance' } }
    }
    stage('Auto deploy the sealed six-task DAG') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py deploy' } }
    }
    stage('Run native Feast RAG validation and DataHub readback') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'iot-airflow', usernameVariable: 'IOT_AIRFLOW_USER', passwordVariable: 'IOT_AIRFLOW_PASSWORD')]) {
          dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py verify' }
        }
      }
    }
  }
  post {
    cleanup {
      dir("build-${env.BUILD_NUMBER}") { sh 'python3 scripts/ci-rag-drift.py archive' }
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/outputs/ci-rag/**", allowEmptyArchive: true, fingerprint: true
    }
  }
}
