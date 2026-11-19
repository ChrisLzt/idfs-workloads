package Vdb;

import User.UserCmd;
import idfsfast.FastWorkload;
import idfsgraphzipf.ZipfDistribution;
import java.util.ArrayList;
import java.util.SplittableRandom;

/** The actual Vdbench concat translation using in-memory command structs. */
public final class FastRoutingProbe {
    private static final long SHARD_BYTES = 512L << 30;
    private static final int TRANSFER_BYTES = 128 << 10;
    private static final int OBJECT_BYTES = 4 << 20;

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }

    private static final class FastGroup extends WG_entry {
        @Override public double[] getXfersizes() { return new double[]{TRANSFER_BYTES}; }
    }

    public static void main(String[] args) {
        Validate.setSdConcatenation();
        WG_entry wg = new FastGroup();
        wg.wd_name = "fast";
        wg.sds_in_concatenation = new ArrayList<>();
        for (int i = 0; i < 8; i++) {
            SD_entry file = new SD_entry();
            file.sd_name = "fast0" + i;
            file.end_lba = SHARD_BYTES;
            file.csd_start_lba = -1;
            wg.sds_in_concatenation.add(file);
        }
        wg.sd_used = new SD_entry();
        wg.sd_used.sd_name = "concat";
        wg.sd_used.concatenated_sd = true;
        wg.sd_used.end_lba = 8 * SHARD_BYTES;
        ConcatSds.calculateLbaRanges(wg);
        Work work = new Work();
        work.wgs_for_slave = new ArrayList<>();
        work.wgs_for_slave.add(wg);
        SlaveWorker.work = work;
        JniIndex.createIndexes(work);
        for (int i = 0; i < 8; i++) {
            int first = (int)(i * SHARD_BYTES / OBJECT_BYTES);
            int last = first + (int)(SHARD_BYTES / OBJECT_BYTES) - 1;
            for (int object : new int[]{first, last})
                for (int block = 0; block < 3; block++)
                    for (boolean read : new boolean[]{true, false}) {
                        long logical = FastWorkload.offset(object, block);
                        Cmd_entry cmd = new Cmd_entry();
                        cmd.sd_ptr = wg.sd_used;
                        cmd.concat_sd = wg.sd_used;
                        cmd.cmd_wg = wg;
                        UserCmd user = new UserCmd(cmd);
                        user.setLba(logical);
                        user.setReadFlag(read);
                        user.setXfersize(TRANSFER_BYTES);
                        user.updateCommand();
                        ConcatSds.translateConcatToRealSd(cmd, new SD_entry(), new ConcatLbaSearch(), false);
                        check(cmd.sd_ptr == wg.sds_in_concatenation.get(i));
                        check(cmd.jni_index == i);
                        check(cmd.cmd_read_flag == read);
                        check(cmd.cmd_xfersize == TRANSFER_BYTES);
                        check(cmd.cmd_lba == logical - i * SHARD_BYTES);
                        check(cmd.cmd_lba % OBJECT_BYTES == (block + 1) * TRANSFER_BYTES);
                        check(cmd.cmd_lba + cmd.cmd_xfersize <= SHARD_BYTES - 4096);
                    }
        }

        ZipfDistribution zipf = new ZipfDistribution(1048596);
        boolean[] top = new boolean[ZipfDistribution.OBJECTS];
        int topCount = ZipfDistribution.OBJECTS / 5;
        for (int r = 0; r < topCount; r++) top[zipf.objectAtRank(r)] = true;
        int samples = 600000, hot = 0, reads = 0;
        int[] offsets = new int[3];
        UserCmd request = new UserCmd(new Cmd_entry());
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
        System.out.printf("FAST route and sampling passed; read=%.4f, hot20=%.4f (expected %.4f)%n",
                          (double)reads / samples, (double)hot / samples, zipf.cumulative(topCount - 1));
    }
}
