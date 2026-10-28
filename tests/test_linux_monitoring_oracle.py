"""Known-answer Linux arithmetic and invalid-window checks without a live exporter or Prometheus."""
from copy import deepcopy
import unittest
from tests.linux_monitoring_oracle import expected_linux,compare_linux


def fixture():
    """Eight idle counters at75%, two devices and three filesystems with deliberately distinct capacity ratios."""
    raw={}
    def vector(name,rows):raw[name]={'data':{'resultType':'vector','result':[{'metric':labels,'value':[30,str(value)]} for labels,value in rows]}}
    def matrix(name,rates):raw[name]={'data':{'resultType':'matrix','result':[{'metric':{'device':str(i)},'values':[[t,str(100+rate*t)] for t in range(1,30,2)]} for i,rate in enumerate(rates)]}}
    matrix('node_cpu_seconds_total',[.75]*8);vector('node_memory_MemAvailable_bytes',[({},400)]);vector('node_memory_MemTotal_bytes',[({},1000)])
    mounts=['/','/srv/iot-data','/srv/iot-fast'];vector('node_filesystem_size_bytes',[({'mountpoint':m},1000) for m in mounts]);vector('node_filesystem_avail_bytes',[({'mountpoint':m},free) for m,free in zip(mounts,[200,500,900])])
    for name,values in [('node_disk_read_bytes_total',[10,5]),('node_disk_written_bytes_total',[10,10]),('node_network_receive_bytes_total',[15,5]),('node_network_transmit_bytes_total',[4,1])]:matrix(name,values)
    vector('up',[({},1)]);vector('node_scrape_collector_success',[({'collector':c},1) for c in ['cpu','meminfo','filesystem','diskstats','netdev']]);return raw


class LinuxOracleTests(unittest.TestCase):
    """Check independently specified percentages/rates and preserve the missing-data boundary."""
    def test_known_capacity_and_io_values(self):
        expected=expected_linux(fixture(),30)
        self.assertEqual(expected[1,'A'],[25]);self.assertEqual(expected[2,'A'],[60]);self.assertEqual(expected[3,'A'],[400])
        for mount,value in [('/',80),('/srv/iot-data',50),('/srv/iot-fast',10)]:self.assertAlmostEqual(expected[4,'A'][mount],value)
        for key,value in [((6,'A'),15),((6,'B'),20),((7,'A'),20),((7,'B'),5),((8,'A'),1),((9,'A'),1)]:self.assertAlmostEqual(expected[key][0],value)

    def test_incomplete_window_is_not_a_zero_rate(self):
        raw=fixture();raw['node_cpu_seconds_total']['data']['result'][0]['values'].pop()
        with self.assertRaises(AssertionError):expected_linux(raw,30)

    def test_duplicate_or_missing_filesystem_is_rejected(self):
        raw=fixture();raw['node_filesystem_avail_bytes']['data']['result'].append(deepcopy(raw['node_filesystem_avail_bytes']['data']['result'][0]))
        with self.assertRaises(AssertionError):expected_linux(raw,30)

    def test_comparison_binds_mounts_and_rejects_nonfinite(self):
        compare_linux([({'mountpoint':'/'},80)],{'/':80})
        with self.assertRaises(AssertionError):compare_linux([({'mountpoint':'/other'},80)],{'/':80})
        with self.assertRaises(AssertionError):compare_linux([({},float('nan'))],[0])


if __name__=='__main__':unittest.main()
