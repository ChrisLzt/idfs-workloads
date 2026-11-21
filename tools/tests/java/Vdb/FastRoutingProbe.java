package Vdb;

import User.UserCmd;
import User.UserDeviceInfo;
import User.WorkloadInfo;
import idfscommon.ContainerPrepare;
import idfsfast.FastWorkload;
import idfsgraphzipf.ZipfDistribution;
import java.util.SplittableRandom;

/** Check single-SD addresses in stock Vdbench command structs. */
public final class FastRoutingProbe {
    private static final int TRANSFER_BYTES = 128 << 10;
    private static final int OBJECT_BYTES = 4 << 20;

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }

    private static final class FastGroup extends WG_entry {
        @Override public double[] getXfersizes() { return new double[]{TRANSFER_BYTES}; }
    }

    private static void probePreparation(long capacity, int stride, int payload,
                                         int offset, long first, long end) {
        ContainerPrepare prepare = new ContainerPrepare();
        String fingerprint = "jarsha" + System.getProperty("fast.test.sha256");
        String[] parameters = {"extent" + capacity, "stride" + stride, "offset" + offset,
                               "payload" + payload, "first" + first, "end" + end, fingerprint};
        check(prepare.parser(parameters));
        WG_entry group = new WG_entry() {
            @Override public double[] getXfersizes() { return new double[]{payload}; }
        };
        group.wd_name = "prepare";
        group.seekpct = -1;
        group.readpct = 0;
        group.sd_used = new SD_entry();
        group.sd_used.lun = "/OFFLINE_ONLY_NO_DATA/container";
        group.sd_used.sd_name = "prepare";
        group.sd_used.end_lba = capacity;

        final long[] next = {first};
        prepare.initialize(new WorkloadInfo(group) {
            @Override public boolean scheduleIO(long delta, long address, int size, boolean read) {
                long expected = next[0]++ * stride + offset;
                check(delta == 0 && !read && address == expected);
                check(size == Math.min(payload, capacity - address));
                return true;
            }
        });
        check(prepare.generate());
        check(next[0] == end);
        parameters[5] = "end" + ((capacity - 1) / stride + 2);
        check(!new ContainerPrepare().parser(parameters));
    }

    public static void main(String[] args) {
        WG_entry wg = new FastGroup();
        wg.wd_name = "fast";
        wg.readpct = 90;
        SD_entry file = new SD_entry();
        file.sd_name = "fast";
        file.lun = "/OFFLINE_ONLY_NO_DATA/fast.bin";
        file.end_lba = ZipfDistribution.CAPACITY;
        wg.sd_used = file;
        SlaveWorker.work = new Work();
        UserDeviceInfo.clearDeviceList();
        probePreparation(ZipfDistribution.CAPACITY, OBJECT_BYTES, 400 << 10, TRANSFER_BYTES,
                         ZipfDistribution.OBJECTS - 2, ZipfDistribution.OBJECTS);
        // A dense final transfer can be shorter and must still end at EOF.
        probePreparation(3 * OBJECT_BYTES + 4096L, OBJECT_BYTES, OBJECT_BYTES, 0, 0, 4);
        UserDeviceInfo.clearDeviceList();
        FastWorkload adapter = new FastWorkload();
        adapter.initialize(new WorkloadInfo(wg));

        for (int object : new int[]{0, ZipfDistribution.OBJECTS - 1}) {
            for (int block = 0; block < 3; block++) {
                long address = FastWorkload.offset(object, block);
                check(address == (long)object * OBJECT_BYTES + (block + 1L) * TRANSFER_BYTES);
                check(address > 0 && address + TRANSFER_BYTES <= file.end_lba);
            }
        }
        Cmd_entry command = new Cmd_entry();
        command.sd_ptr = file;
        UserCmd request = new UserCmd(command);
        check(adapter.preIO(request));
        request.updateCommand();
        check(command.sd_ptr == file && command.cmd_xfersize == TRANSFER_BYTES);
        check(command.cmd_lba > 0 && command.cmd_lba + command.cmd_xfersize <= file.end_lba);

        ZipfDistribution zipf = new ZipfDistribution(1048596);
        boolean[] top = new boolean[ZipfDistribution.OBJECTS];
        int topCount = ZipfDistribution.OBJECTS / 5;
        for (int r = 0; r < topCount; r++) top[zipf.objectAtRank(r)] = true;
        int samples = 600000, hot = 0, reads = 0;
        int[] offsets = new int[3];
        request = new UserCmd(new Cmd_entry());
        SplittableRandom random = new SplittableRandom(7);
        for (int i = 0; i < samples; i++) {
            FastWorkload.sample(request, zipf, random);
            if (request.isRead()) reads++;
            if (top[(int)(request.getLba() / OBJECT_BYTES)]) hot++;
            check(request.getXfersize() == TRANSFER_BYTES);
            offsets[(int)(request.getLba() % OBJECT_BYTES / TRANSFER_BYTES) - 1]++;
        }
        check(Math.abs((double)reads / samples - .90) < .002);
        check(Math.abs((double)hot / samples - zipf.cumulative(topCount - 1)) < .002);
        for (int n : offsets) check(Math.abs((double)n / samples - 1.0 / 3) < .003);
        System.out.printf("FAST single-SD addresses and sampling passed; read=%.4f, hot20=%.4f (expected %.4f)%n",
                          (double)reads / samples, (double)hot / samples, zipf.cumulative(topCount - 1));
    }
}
