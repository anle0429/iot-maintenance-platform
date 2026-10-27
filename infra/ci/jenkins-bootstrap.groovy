// Initialize a private authenticated controller; read bootstrap inputs only from its protected runtime mount.
import jenkins.model.Jenkins
import jenkins.model.JenkinsLocationConfiguration
import jenkins.install.InstallState
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.csrf.DefaultCrumbIssuer
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.slaves.EnvironmentVariablesNodeProperty
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import groovy.json.JsonSlurper

def instance = Jenkins.get()
def input = new JsonSlurper().parse(new File('/run/iot-ci-secrets.json'))
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount('learner', input.admin_password)
instance.setSecurityRealm(realm)
def policy = new FullControlOnceLoggedInAuthorizationStrategy()
// Read-only build views remain behind IAP; all build/configuration actions require login.
policy.setAllowAnonymousRead(true)
instance.setAuthorizationStrategy(policy)
instance.setCrumbIssuer(new DefaultCrumbIssuer(true))
instance.setNumExecutors(1)
instance.setSlaveAgentPort(-1)
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
JenkinsLocationConfiguration.get().setUrl('http://127.0.0.1:18787/')
def store = SystemCredentialsProvider.getInstance().getStore()
if (!store.getCredentials(Domain.global()).any { it.id == 'iot-airflow' }) {
  store.addCredentials(Domain.global(), new UsernamePasswordCredentialsImpl(
    CredentialsScope.GLOBAL, 'iot-airflow', 'Private coursework Airflow API', 'learner', input.airflow_password))
}
def environment = new EnvironmentVariablesNodeProperty()
environment.envVars.put('IOT_DEPLOY_ROOT', input.deploy_root)
environment.envVars.put('IOT_AIRFLOW_URL', 'http://airflow-api:8080')
instance.globalNodeProperties.replace(environment)
def job = instance.getItem('iot-dp1-dp3') ?: instance.createProject(WorkflowJob, 'iot-dp1-dp3')
job.addProperty(new ParametersDefinitionProperty(new StringParameterDefinition('SOURCE_COMMIT', input.source_commit, 'Sealed trusted source commit on this VM')))
job.setDefinition(new CpsFlowDefinition(new File('/opt/iot-ci/Jenkinsfile').getText('UTF-8'), true))
job.save()
instance.save()
println('Private authenticated coursework pipeline initialized; credential values omitted.')
