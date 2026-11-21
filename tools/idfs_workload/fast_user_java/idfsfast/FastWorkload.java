package idfsfast;

import User.UserClass;
import User.UserCmd;
import User.UserDeviceInfo;
import User.WorkloadInfo;
import idfscommon.RuntimeIdentity;
import idfsgraphzipf.ZipfDistribution;
import java.util.SplittableRandom;

/** Draw one global object and one complete aligned block per submitted I/O. */
public final class FastWorkload extends UserClass {
    private static final int TRANSFER_BYTES = 128 << 10;
    private static final int FULL_BLOCKS = (400 << 10) / TRANSFER_BYTES;
    private static final long SEED = 1048596L;

    private static final class Distribution {
        static final ZipfDistribution ZIPF = new ZipfDistribution(SEED);
    }

    private WorkloadInfo work;
    private ThreadLocal<SplittableRandom> streams;

    @Override public boolean parser(String[] args) {
        return args.length == 1 && RuntimeIdentity.matchesJar(FastWorkload.class, args[0]);
    }

    @Override public UserDeviceInfo initialize(WorkloadInfo info) {
        if (info.getSd().concatenated_sd || info.getSd().end_lba != ZipfDistribution.CAPACITY
                || info.getAverageXferSize() != TRANSFER_BYTES || info.getRdPct() != 90) {
            throw new IllegalArgumentException("FAST requires a 4 TiB SD, 128 KiB transfer and rdpct=90");
        }
        long hash = 0xcbf29ce484222325L;
        String device = getSlaveCount() <= 1 ? "fast" : info.getSd().sd_name;
        String identity = getSlaveLabel() + "/" + device;
        for (int i = 0; i < identity.length(); i++) {
            hash = (hash ^ identity.charAt(i)) * 0x100000001b3L;
        }
        SplittableRandom source = new SplittableRandom(hash ^ SEED);
        streams = ThreadLocal.withInitial(() -> {
            synchronized (source) {
                return source.split();
            }
        });
        work = info;
        return super.initialize(info);
    }

    @Override public boolean generate() {
        work.setFirstStartDelta(0);
        long count = 0;
        while (!isWorkloadDone() && work.scheduleIO(0, TRANSFER_BYTES, TRANSFER_BYTES, true)) {
            count++;
        }
        work.setEOF(count);
        return count > 0;
    }

    public static long offset(int object, int block) {
        return (long)object * ZipfDistribution.OBJECT_BYTES + (long)(block + 1) * TRANSFER_BYTES;
    }

    public static void sample(UserCmd command, ZipfDistribution zipf, SplittableRandom stream) {
        int object = zipf.objectAtRank(zipf.rank(stream.nextDouble()));
        command.setLba(offset(object, stream.nextInt(FULL_BLOCKS)));
        command.setXfersize(TRANSFER_BYTES);
        command.setReadFlag(stream.nextInt(10) != 0);
    }

    @Override public boolean preIO(UserCmd command) {
        sample(command, Distribution.ZIPF, streams.get());
        return true;
    }

    @Override public boolean postIO(UserCmd command) { return true; }

    @Override public void passData(String data) { }
}
