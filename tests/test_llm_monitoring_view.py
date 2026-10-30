"""Check the credential-bearing viewer's destination and write boundaries without starting a server."""
from pathlib import Path
import importlib.util,unittest
spec=importlib.util.spec_from_file_location('monitoring_view',Path(__file__).resolve().parents[1]/'scripts/view-llm-monitoring.py')
view=importlib.util.module_from_spec(spec);spec.loader.exec_module(view)


class MonitoringViewTests(unittest.TestCase):
    """Separate native reads/query POSTs from foreign-origin and administrative requests."""
    def test_view_and_query_partitions(self):
        """Every fixed role permits reads; only Grafana roles permit the native read-only query endpoint."""
        for role,port in view.PORTS.items():
            host='127.0.0.1:'+str(port)
            with self.subTest(role=role):
                self.assertTrue(view.allowed(role,host,None,'/d/iot-llm-runtime','GET'))
                self.assertEqual(view.allowed(role,host,'http://'+host,'/api/ds/query?ds_type=prometheus','POST'),role!='traces')
                for path,method in [('/api/dashboards/db','POST'),('/api/ds/query','DELETE'),('http://external.example/','GET'),('//external.example/','GET'),('/\\external.example','GET')]:
                    self.assertFalse(view.allowed(role,host,None,path,method))
                self.assertFalse(view.allowed(role,host,'https://external.example','/api/ds/query','POST'))
                self.assertFalse(view.allowed(role,'external.example',None,'/','GET'))
        self.assertFalse(view.allowed('other','127.0.0.1:18100',None,'/','GET'))


if __name__=='__main__':unittest.main()
