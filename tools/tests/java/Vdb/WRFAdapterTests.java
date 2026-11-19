package Vdb;

import idfswrf.*;
import User.*;
import java.nio.file.*;
import java.util.*;

/** Real stock parser, loader and UserCmd; capture replaces the physical I/O boundary. */
public final class WRFAdapterTests {
    private static final class WG extends WG_entry {public double[] getXfersizes(){return new double[]{1048576};}}
    private static final class Capture extends WorkloadInfo {
        final WRFTests.Clock clock;TimelineWorkload adapter;int calls;long eof=-1;
        Capture(WG_entry wg,WRFTests.Clock clock){super(wg);this.clock=clock;}
        @Override public boolean scheduleIO(long delta,long offset,int size,boolean read){
            WRFTests.check(delta==0&&offset==0&&size==1048576&&read,"unthrottled buffer-sized queue token");
            clock.now=calls==0?0:510000000;
            Cmd_entry cmd=new Cmd_entry();cmd.cmd_lba=offset;cmd.cmd_xfersize=size;cmd.cmd_read_flag=read;
            UserCmd user=new UserCmd(cmd);adapter.preIO(user);user.updateCommand();
            WRFTests.check(calls==0?(cmd.cmd_read_flag&&cmd.cmd_lba<2*WRFTests.G):(!cmd.cmd_read_flag&&cmd.cmd_lba>=8196*WRFTests.G),"preIO uses execution-time phase instead of queued token");
            adapter.postIO(user);if(++calls==2)SlaveJvm.setWorkloadDone(true);return true;
        }
        @Override public void setEOF(long n){eof=n;}
    }
    public static void main(String[]args)throws Exception {
        Path dir=Files.createTempDirectory(Paths.get(System.getProperty("java.io.tmpdir")),"wrf-adapter-");Path path=WRFTests.fixture(dir);
        Vdbmain.parms_report=new java.io.PrintWriter(new java.io.StringWriter());
        String uri=path.toUri().toString().replace("-","%2D");
        Vdb_scan parsed=Vdb_scan.parms_split("user=(idfswrf.TimelineWorkload,"+uri+",seed7)");
        WRFTests.check(parsed.alpha_count==3&&parsed.num_count==0,"stock5.04.06 tokenizer");
        SD_entry sd=new SD_entry();sd.sd_name="fixture";sd.lun="/OFFLINE_ONLY/absent";sd.end_lba=WRFTests.CAPACITY;
        WG_entry wg=new WG();wg.sd_used=sd;wg.wd_name="synthetic";wg.arrival=0;wg.readpct=50;wg.user_class_parms=Arrays.copyOf(parsed.alphas,parsed.alpha_count);
        SlaveWorker.work=new Work();ControlUsers.clearClasses();UserDeviceInfo.clearDeviceList();SlaveJvm.setWorkloadDone(false);
        WRFTests.check(ControlUsers.createInstance(wg) instanceof TimelineWorkload,"native User loader");
        WRFTests.Clock clock=new WRFTests.Clock();Capture capture=new Capture(wg,clock);TimelineWorkload adapter=new TimelineWorkload(clock);
        WRFTests.check(adapter.parser(Arrays.copyOfRange(parsed.alphas,1,parsed.alpha_count)),"native args accepted");adapter.initialize(capture);capture.adapter=adapter;adapter.generate();
        WRFTests.check(capture.calls==2&&capture.eof==2,"generation respects native stop");
        clock.now=600000000;Cmd_entry cmd=new Cmd_entry();UserCmd user=new UserCmd(cmd);
        adapter.preIO(user);user.updateCommand();
        WRFTests.check(!cmd.cmd_read_flag&&cmd.cmd_lba>=8196*WRFTests.G&&adapter.lateRequests()==1,"late in-flight callback remains a valid final extent");
        WRFTests.Clock earlyClock=new WRFTests.Clock();earlyClock.now=30000000;
        TimelineWorkload early=new TimelineWorkload(earlyClock);
        early.parser(Arrays.copyOfRange(parsed.alphas,1,parsed.alpha_count));early.initialize(new WorkloadInfo(wg));
        // Stock may mark the workload done early while an I/O callback is in flight.
        SlaveJvm.setWorkloadDone(true);Cmd_entry pending=new Cmd_entry();UserCmd pendingUser=new UserCmd(pending);
        early.preIO(pendingUser);pendingUser.updateCommand();
        WRFTests.check(pending.cmd_read_flag&&pending.cmd_lba<2*WRFTests.G,"early stop keeps the current activity; never invents a future history write");
        TimelineWorkload invalid=new TimelineWorkload(clock);invalid.parser(Arrays.copyOfRange(parsed.alphas,1,parsed.alpha_count));
        try{invalid.loadForCapacity(WRFTests.CAPACITY-4096,1048576);throw new AssertionError("wrong capacity accepted");}catch(IllegalArgumentException expected){}
        try{invalid.loadForCapacity(WRFTests.CAPACITY,4096);throw new AssertionError("undersized buffer accepted");}catch(IllegalArgumentException expected){}
        System.out.println("WRF_ADAPTER_TESTS_PASS tokenizer loader generate execution_time mixed_io late_callback no_physical_io=true");
    }
}
