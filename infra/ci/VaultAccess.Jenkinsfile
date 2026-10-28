// Fetch the Airflow password only at execution time using the scoped Vault AppRole held in Jenkins Credentials.
pipeline {
  agent any
  options { timeout(time: 10, unit: 'MINUTES'); disableConcurrentBuilds() }
  stages {
    stage('Prepare sealed Vault consumer source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the audited source') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
          mkdir "build-$BUILD_NUMBER/out"
          chmod 0777 "build-$BUILD_NUMBER/out"
        '''
      }
    }
    stage('Test private transport and redaction contracts') {
      steps { dir("build-${env.BUILD_NUMBER}") { sh 'python3 -m unittest tests.test_vault_client -v' } }
    }
    stage('Read narrow Vault secret and authenticate actual Airflow') {
      steps {
        withCredentials([string(credentialsId: 'iot-vault-approle', variable: 'IOT_VAULT_APPROLE')]) {
          sh '''set +x
            docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --memory 256m --memory-swap 256m --cpus 1 --network host --env IOT_VAULT_APPROLE --env PYTHONPATH=/src --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree,dst=/src,readonly" --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/runtime/jenkins-home-27/workspace/$JOB_NAME/build-$BUILD_NUMBER/out,dst=/out" --mount "type=bind,src=$IOT_DEPLOY_ROOT/outputs/vault-28/resume/ca.crt,dst=/ca.crt,readonly" --entrypoint python iot-coursework-feast-versioned:0.67.0-3.17.2 /src/scripts/vault-ci-consumer.py --address https://10.52.0.1:18200 --ca /ca.crt --output /out/verification.json
          '''
        }
      }
    }
  }
  post { always { archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/out/*.json", allowEmptyArchive: true, fingerprint: true } }
}
