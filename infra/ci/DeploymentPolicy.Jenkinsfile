// Exercise the pinned policy and enforce a real exit-code gate before any deployment command can be reached.
pipeline {
  agent any
  options { timeout(time: 10, unit: 'MINUTES'); disableConcurrentBuilds() }
  stages {
    stage('Prepare sealed policy source') {
      steps {
        script {
          if (!(params.SOURCE_COMMIT ==~ /[0-9a-f]{40}/)) { error('Pin the audited source') }
          if (!(params.SCENARIO in ['safe', 'historical-missing-seccomp'])) { error('Unknown candidate') }
          env.IOT_CI_COMMIT = params.SOURCE_COMMIT
          env.POLICY_INPUT = params.SCENARIO == 'safe' ? 'policy-kubernetes-observed.json' : 'policy-kubernetes-unsafe-historical.json'
        }
        sh '''set +x
          mkdir "build-$BUILD_NUMBER"
          cp -R "$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree/." "build-$BUILD_NUMBER/"
          mkdir "build-$BUILD_NUMBER/out"
          chmod 0777 "build-$BUILD_NUMBER/out"
          python3 - <<'PY'
import os,json,hashlib,pathlib
r=pathlib.Path('build-'+os.environ['BUILD_NUMBER']);seal=json.loads((pathlib.Path(os.environ['IOT_DEPLOY_ROOT'])/'data/ci/source'/os.environ['IOT_CI_COMMIT']/'manifest.json').read_bytes())
for n,h in seal['files'].items():assert hashlib.sha256((r/n).read_bytes()).hexdigest()==h
(r/'out/source-before.json').write_text(json.dumps(seal,indent=2)+'\n')
asset=json.loads((r/'infra/policy/opa-assets.json').read_bytes());binary=pathlib.Path(os.environ['IOT_DEPLOY_ROOT'])/'data/runtime/policy-assets-28/opa';assert hashlib.sha256(binary.read_bytes()).hexdigest()==asset['sha256'];(r/'out/binary.json').write_text(json.dumps(asset,indent=2)+'\n')
PY
        '''
      }
    }
    stage('Known-answer policy contracts') {
      when { expression { params.SCENARIO == 'safe' } }
      steps {
        sh '''set +x
          docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --cpus 1 --memory 512m --memory-swap 512m --tmpfs /tmp:rw,nosuid,nodev,size=32m --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree,dst=/src,readonly" --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/manifest.json,dst=/seal.json,readonly" --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/runtime/policy-assets-28/opa,dst=/opa,readonly" --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/runtime/jenkins-home-27/workspace/$JOB_NAME/build-$BUILD_NUMBER/out,dst=/out" --entrypoint python iot-coursework-feast-versioned:0.67.0-3.17.2 /src/scripts/check-deployment-policy.py --opa /opa --source-manifest /seal.json --output /out/contracts
        '''
      }
    }
    stage('Candidate decision gate') {
      steps {
        sh '''set +x
          set +e
          docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --cpus 1 --memory 512m --memory-swap 512m --tmpfs /tmp:rw,nosuid,nodev,size=32m --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/ci/source/$IOT_CI_COMMIT/tree,dst=/src,readonly" --mount "type=bind,src=$IOT_DEPLOY_ROOT/data/runtime/policy-assets-28/opa,dst=/opa,readonly" --entrypoint /opa iot-coursework-feast-versioned:0.67.0-3.17.2 eval --data /src/infra/policy/deployment.rego --data /src/infra/policy/approved.json --input "/src/tests/fixtures/$POLICY_INPUT" --fail --format=json 'data.iot.deployment.result.allowed == true' > "build-$BUILD_NUMBER/out/candidate-gate.stdout" 2> "build-$BUILD_NUMBER/out/candidate-gate.stderr"
          policy_exit=$?
          printf '%s\n' "$policy_exit" > "build-$BUILD_NUMBER/out/candidate-gate.exit"
          exit "$policy_exit"
        '''
      }
    }
    stage('Candidate accepted for review') {
      steps { echo 'Policy accepted this candidate. This verification job does not deploy resources.' }
    }
  }
  post {
    always {
      sh '''set +x
        python3 - <<'PY'
import os,json,hashlib,pathlib
r=pathlib.Path('build-'+os.environ['BUILD_NUMBER']);path=r/'out/source-before.json'
if path.exists():
 seal=json.loads(path.read_bytes())
 for n,h in seal['files'].items():assert hashlib.sha256((r/n).read_bytes()).hexdigest()==h
 (r/'out/source-after.json').write_text(json.dumps(seal,indent=2)+'\n')
PY
      '''
      archiveArtifacts artifacts: "build-${env.BUILD_NUMBER}/out/**/*", allowEmptyArchive: true, fingerprint: true
    }
  }
}
