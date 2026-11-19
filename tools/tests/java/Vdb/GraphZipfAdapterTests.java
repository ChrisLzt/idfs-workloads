package Vdb;

import User.*;
import idfsgraphzipf.*;
import java.util.Arrays;

/** Original stock parser/loader/UserCmd; replace only the scheduling boundary. */
public final class GraphZipfAdapterTests {
    private static final class WG extends WG_entry {
        public double[] getXfersizes() { return new double[] {32}; }
    }
    private static final class Capture extends WorkloadInfo {
        ZipfWorkload adapter;
        long eof = -1;
        int calls;
        boolean subSector, highOffset;
        final java.util.HashSet<Long> offsets = new java.util.HashSet<>();
        Capture(WG_entry wg) { super(wg); }
        public boolean scheduleIO(long delta, long offset, int size, boolean read) {
            ZipfTests.check(delta == 0 && size == 32 && read, "max-rate, read-only32B queue token");
            Cmd_entry c = new Cmd_entry();
            c.cmd_lba = offset; c.cmd_xfersize = size; c.cmd_read_flag = read;
            UserCmd u = new UserCmd(c);
            ZipfTests.check(adapter.preIO(u), "callback accepted");
            u.updateCommand(); adapter.postIO(u);
            ZipfTests.check(c.cmd_read_flag && c.cmd_xfersize == 32, "original command preserves32B read");
            ZipfTests.check(c.cmd_lba >= 0 && c.cmd_lba + 32 <= 4398046511104L, "original long LBA");
            subSector |= c.cmd_lba % 512 != 0;
            highOffset |= c.cmd_lba > 2147483648L;
            offsets.add(c.cmd_lba);
            if (++calls == 64) SlaveJvm.setWorkloadDone(true);
            return true;
        }
        public void setEOF(long count) { eof = count; }
    }
    public static void main(String[] args) {
        System.setProperty("graph.zipf.client", "offline-client");
        Vdbmain.parms_report = new java.io.PrintWriter(new java.io.StringWriter());
        String fingerprint = "jarsha" + System.getProperty("graph.test.sha256");
        Vdb_scan p = Vdb_scan.parms_split("user=(idfsgraphzipf.ZipfWorkload,zipf,seed20261031,"+fingerprint+")");
        ZipfTests.check(p.alpha_count == 4 && p.num_count == 0, "stock tokenizer preserves User parameters");
        SD_entry sd = new SD_entry(); sd.sd_name = "graph_000";
        sd.lun = "/OFFLINE_ONLY_NO_DATA/test.graph.edge.props"; sd.end_lba = 4398046511104L;
        WG_entry wg = new WG(); wg.sd_used = sd; wg.wd_name = "zipf"; wg.readpct = 100;
        wg.user_class_parms = Arrays.copyOf(p.alphas, p.alpha_count);
        SlaveWorker.work = new Work(); ControlUsers.clearClasses(); UserDeviceInfo.clearDeviceList();
        SlaveJvm.setWorkloadDone(false);
        UserClass loaded = ControlUsers.createInstance(wg);
        ZipfTests.check(loaded instanceof ZipfWorkload, "stock loader loads external extension");
        ZipfWorkload adapter = new ZipfWorkload();
        ZipfTests.check(adapter.parser(Arrays.copyOfRange(p.alphas, 1, p.alpha_count)), "valid arguments");
        ZipfTests.check(!new ZipfWorkload().parser(new String[] {"zipf", "invalid", fingerprint}), "invalid seed rejected");
        ZipfTests.check(!new ZipfWorkload().parser(new String[] {"zipf", "seed20261031", "jarsha"+String.format("%064d",0)}), "stale remote extension rejected");
        Capture capture = new Capture(wg); adapter.initialize(capture); capture.adapter = adapter;
        ZipfTests.check(adapter.generate(), "generator reached stock schedule API");
        ZipfTests.check(capture.calls == 64 && capture.eof == 64, "RD-controlled stop without output quota");
        ZipfTests.check(capture.subSector && capture.highOffset, "real command retains small offsets across4TiB");
        ZipfTests.check(capture.offsets.size() > 60, "callback must not reset random state");
        long[][] parallel = new long[2][128];
        Thread[] readers = new Thread[2];
        for (int t = 0; t < 2; t++) {
            final int index = t;
            readers[t] = new Thread(() -> {
                for (int i = 0; i < 128; i++) {
                    Cmd_entry c = new Cmd_entry(); UserCmd u = new UserCmd(c);
                    adapter.preIO(u); u.updateCommand(); parallel[index][i] = c.cmd_lba;
                }
            });
            readers[t].start();
        }
        for (Thread reader : readers) {
            try { reader.join(); } catch (InterruptedException e) { throw new AssertionError(e); }
        }
        ZipfTests.check(!Arrays.equals(parallel[0], parallel[1]), "IO threads must use independent streams");
        System.out.println("GRAPH_ZIPF_ADAPTER_PASS physical_scheduling_replaced=true no_storage_io=true");
    }
}
