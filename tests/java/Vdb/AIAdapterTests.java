package Vdb;

import idfsai.*;
import User.*;
import java.nio.file.*;
import java.util.*;

/** Stock loader and UserCmd updates; only the physical-I/O boundary is replaced. */
public final class AIAdapterTests {
    private static final class WG extends WG_entry {
        int maximum;
        WG(int maximum) { this.maximum = maximum; }
        public double[] getXfersizes() { return new double[]{maximum}; }
    }
    private static final class Capture extends WorkloadInfo {
        final AITests.Clock clock;
        final int maximum;
        TimelineWorkload adapter;
        int calls, reads, writes;
        long eof = -1;
        Capture(WG_entry wg, AITests.Clock clock, int maximum) { super(wg); this.clock = clock; this.maximum = maximum; }
        @Override public boolean scheduleIO(long delta, long offset, int size, boolean read) {
            AITests.check(delta == 0 && offset == 0 && size == maximum && read, "unthrottled maximum-buffer queue token");
            clock.now = calls * 1000000L;
            Cmd_entry cmd = new Cmd_entry(); cmd.cmd_lba = offset; cmd.cmd_xfersize = size; cmd.cmd_read_flag = read;
            UserCmd user = new UserCmd(cmd); adapter.preIO(user); user.updateCommand();
            AITests.check(cmd.cmd_lba >= 0 && cmd.cmd_lba % 4096 == 0 && cmd.cmd_xfersize <= maximum,
                          "User supplies aligned container address and bounded transfer");
            if (cmd.cmd_read_flag) reads++; else writes++;
            adapter.postIO(user);
            if (++calls == 512) SlaveJvm.setWorkloadDone(true);
            return true;
        }
        @Override public void setEOF(long count) { eof = count; }
    }
    public static void main(String[] args) throws Exception {
        for (int c = 0; c < args.length; c++) {
            Path path = Paths.get(args[c]);
            String[] header = Files.readAllLines(path).get(0).split("\t");
            long capacity = Long.parseLong(header[2]); int maximum = Integer.parseInt(header[6]);
            Vdbmain.parms_report = new java.io.PrintWriter(new java.io.StringWriter());
            String uri = path.toUri().toString().replace("-", "%2D");
            Vdb_scan parsed = Vdb_scan.parms_split("user=(idfsai.TimelineWorkload," + uri + ",current,seed7)");
            AITests.check(parsed.alpha_count == 4 && parsed.num_count == 0, "original5.04.06 User tokenizer");
            SD_entry sd = new SD_entry(); sd.sd_name = "fixture"; sd.lun = "/OFFLINE_ONLY/absent"; sd.end_lba = capacity;
            WG_entry wg = new WG(maximum); wg.sd_used = sd; wg.wd_name = "ses"; wg.arrival = 0; wg.readpct = 50;
            wg.user_class_parms = Arrays.copyOf(parsed.alphas, parsed.alpha_count);
            SlaveWorker.work = new Work(); ControlUsers.clearClasses(); UserDeviceInfo.clearDeviceList(); SlaveJvm.setWorkloadDone(false);
            AITests.check(ControlUsers.createInstance(wg) instanceof TimelineWorkload, "native User class loader");
            AITests.Clock clock = new AITests.Clock(); TimelineWorkload adapter = new TimelineWorkload(clock);
            Capture capture = new Capture(wg, clock, maximum); capture.adapter = adapter;
            String[] userArgs = Arrays.copyOfRange(parsed.alphas, 1, parsed.alpha_count);
            AITests.check(adapter.parser(userArgs), "User arguments accepted"); adapter.initialize(capture); adapter.generate();
            AITests.check(capture.calls == 512 && capture.eof == 512 && capture.reads > 0 && capture.writes > 0,
                          "stock User route emits both operations and finalizes its queue without a quota");
            clock.now = 600000000;
            Cmd_entry late = new Cmd_entry(); UserCmd pending = new UserCmd(late); adapter.preIO(pending); pending.updateCommand();
            AITests.check(late.cmd_lba + late.cmd_xfersize <= capacity && adapter.lateRequests() == 1,
                          "late in-flight request cannot escape the container");
            TimelineWorkload invalid = new TimelineWorkload(clock); invalid.parser(userArgs);
            try { invalid.loadForCapacity(capacity - 4096, maximum); throw new AssertionError("wrong capacity accepted"); }
            catch (IllegalArgumentException expected) { }
            try { invalid.loadForCapacity(capacity, 4096); throw new AssertionError("short buffer accepted"); }
            catch (IllegalArgumentException expected) { }
            String[] badArgs = userArgs.clone(); badArgs[1] = "unknown";
            AITests.check(!new TimelineWorkload(clock).parser(badArgs), "unknown profile rejected");
        }
        System.out.println("AI_ADAPTER_TESTS_PASS original50406 loader tokenizer read_write queue late_callback no_physical_io=true");
    }
}
