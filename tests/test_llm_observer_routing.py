"""Check that observation planning cannot silently broaden trace routing or rewrite model and controller settings."""
from copy import deepcopy
import unittest
from infra.llm_observer_routing import TRACE_CLUSTER,TRACE_ENV,TRACE_PATH,controller_trace_environment,private_trace_route


class ObserverRouteTests(unittest.TestCase):
    """Exercise routing isolation, old-route preservation and conflicting controller settings without running infrastructure."""
    def fixture(self):
        """Represent a model listener plus an unrelated readiness listener and original destination cluster."""
        return {'static_resources':{'listeners':[
            {'name':'readiness','address':{'socket_address':{'port_value':19001}},'filter_chains':[]},
            {'name':'model','address':{'socket_address':{'port_value':8081}},'filter_chains':[{'filters':[{
                'name':'envoy.filters.network.http_connection_manager','typed_config':{
                    'route_config':{'virtual_hosts':[{'name':'model','domains':['*'],'routes':[
                        {'match':{'prefix':'/'},'route':{'cluster':'original_destination_cluster','timeout':'86400s'}}]}]},
                    'http_filters':[{'name':'envoy.filters.http.ext_proc','typed_config':{'failure_mode_allow':False}}]}}]}]},
        ],'clusters':[{'name':'original_destination_cluster','type':'ORIGINAL_DST'}]},'admin':{'unchanged':True}}

    def test_only_exact_post_trace_route_bypasses_picker(self):
        """Keep the catch-all model route and every original cluster unchanged, adding only the fixed private trace destination."""
        original=self.fixture();saved=deepcopy(original);result=private_trace_route(original)
        self.assertEqual(original,saved)
        manager=result['static_resources']['listeners'][1]['filter_chains'][0]['filters'][0]['typed_config']
        routes=manager['route_config']['virtual_hosts'][0]['routes']
        self.assertEqual(routes[0]['match'],{'path':TRACE_PATH,'headers':[{'name':':method','string_match':{'exact':'POST'}}]})
        self.assertEqual(routes[0]['route'],{'cluster':TRACE_CLUSTER,'prefix_rewrite':'/v1/traces','timeout':'15s'})
        self.assertTrue(routes[0]['typed_per_filter_config']['envoy.filters.http.ext_proc']['disabled'])
        self.assertEqual(routes[1:],saved['static_resources']['listeners'][1]['filter_chains'][0]['filters'][0]['typed_config']['route_config']['virtual_hosts'][0]['routes'])
        self.assertEqual(result['static_resources']['clusters'][:-1],saved['static_resources']['clusters'])
        self.assertEqual(result['admin'],saved['admin'])
        with self.assertRaises(ValueError):private_trace_route(result)

    def test_different_routes_or_picker_are_preserved_by_refusal(self):
        """Reject ambiguous layouts and altered catch-all routing instead of guessing which existing route to replace."""
        for change in ['listener','routes','cluster','picker','host']:
            with self.subTest(change=change):
                config=self.fixture();listener=config['static_resources']['listeners'][1]
                manager=listener['filter_chains'][0]['filters'][0]['typed_config'];host=manager['route_config']['virtual_hosts'][0]
                if change=='listener':config['static_resources']['listeners'].append(deepcopy(listener))
                elif change=='routes':host['routes'].append(deepcopy(host['routes'][0]))
                elif change=='cluster':host['routes'][0]['route']['cluster']='different'
                elif change=='picker':manager['http_filters']=[]
                else:host['domains']=['different']
                saved=deepcopy(config)
                with self.assertRaises(ValueError):private_trace_route(config)
                self.assertEqual(config,saved)

    def test_controller_changes_are_limited_idempotent_and_content_free(self):
        """Keep original credential references and business settings while refusing duplicate or conflicting trace values."""
        original=[{'name':'DATABASE_URL_FILE','value':'/private/url'},{'name':'NAMESPACE','valueFrom':{'fieldRef':{'fieldPath':'metadata.namespace'}}}]
        saved=deepcopy(original);result=controller_trace_environment(original)
        self.assertEqual(original,saved);self.assertEqual(result[:2],original)
        self.assertEqual({v['name']:v['value'] for v in result[2:]},TRACE_ENV)
        self.assertEqual(controller_trace_environment(result),result)
        for environment in [original+original,[{'name':'OTEL_TRACING_ENABLED','value':'false'}],
                            [{'name':'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT','value':'true'}]]:
            with self.subTest(environment=environment):
                with self.assertRaises(ValueError):controller_trace_environment(environment)


if __name__=='__main__':unittest.main()
