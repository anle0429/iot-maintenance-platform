"""Run the OPA agent pipeline with an additional MLflow prompt admission stage."""

from pathlib import Path
import argparse
import importlib.util
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.prompt_registry import ROLES, prompt_pins, verify_prompts

SPEC = importlib.util.spec_from_file_location('agent_policy_pipeline', ROOT / 'scripts/ci-agents-policy.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)

TRACKING_URI = 'http://127.0.0.1:15000'
VERSION_ENV = 'IOT_AGENT_PROMPT_VERSIONS'
REQUIRED = {
    'scripts/ci-agents-prompts.py',
    'agents/prompt_registry.py',
    'infra/ci/AgentsPrompts.Jenkinsfile',
}


class PromptContext(POLICY.PolicyContext):
    """Bind prompt admission to the same job, source seal and release as OPA."""

    driver = '/src/scripts/ci-agents-prompts.py'

    def source_check(self):
        """Require the new pipeline and prompt gate in the sealed source packet."""
        super().source_check()
        if not REQUIRED <= self.seal['files'].keys():
            raise ValueError('Missing prompt admission source inputs')

    def control(self, phase, cluster=False, memory='2g', extra=None):
        """Pass the same reviewed version pins into the retained control image."""
        super().control(phase, cluster, memory, [*(extra or []), '--env', VERSION_ENV])

    def expected_prompts(self):
        """Read the response policy also consumed by the unchanged Helm renderer."""
        policy = json.loads((ROOT / 'agents/response-policy.json').read_bytes())
        versions = dict(zip(ROLES, map(int, os.environ[VERSION_ENV].split(',')), strict=True))
        return policy['prompts'], versions

    def policy_receipt(self):
        """Require this build's prompt receipt before native deployment begins."""
        receipt = super().policy_receipt()
        prompts, versions = self.expected_prompts()
        admitted = json.loads((self.out / 'prompts.json').read_bytes())
        expected = {'schema_version': 1, 'phase': 'prompts', 'passed': True, **self.identity,
                    'source_digest': self.source_digest, 'prompts': prompt_pins(prompts, versions)}
        if admitted != expected or admitted['passed'] is not True:
            raise ValueError('The admitted prompt versions or source changed')
        return receipt


def run(phase):
    """Run prompt admission after controller tests and before release packaging."""
    POLICY.PolicyContext = PromptContext
    if phase not in {'prompts', 'prompts-host'}:
        return POLICY.run(phase)
    context = PromptContext()
    context.prior('test')
    if phase == 'prompts':
        POLICY.BASE.SHARED.real_admission(context, 900)
        context.native('prompts-host')
    else:
        import mlflow
        mlflow.set_tracking_uri(TRACKING_URI)
        mlflow.set_registry_uri(TRACKING_URI)
        prompts, versions = context.expected_prompts()
        admitted = verify_prompts(prompts, versions, mlflow.genai.load_prompt)
        context.phase('prompts', {'source_digest': context.source_digest, 'prompts': admitted})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['admission', 'prompts', 'prompts-host', 'test', 'test-host', 'build',
        'build-host', 'policy', 'policy-host', 'deploy', 'admitted-deploy-host', 'verify', 'verify-host',
        'rollback', 'rollback-host', 'archive'])
    run(parser.parse_args().phase)
