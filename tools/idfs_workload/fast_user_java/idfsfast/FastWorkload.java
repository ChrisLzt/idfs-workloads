package idfsfast;

import User.UserClass;
import User.UserCmd;
import User.UserDeviceInfo;
import User.WorkloadInfo;
import idfsgraphzipf.ZipfDistribution;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
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
        if (args.length != 1 || !args[0].matches("jarsha[0-9a-f]{64}")) return false;
        try {
            Path jar = Paths.get(FastWorkload.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = Files.newInputStream(jar)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
            }
            StringBuilder fingerprint = new StringBuilder("jarsha");
            for (byte b : digest.digest()) {
                fingerprint.append(Character.forDigit((b >>> 4) & 15, 16));
                fingerprint.append(Character.forDigit(b & 15, 16));
            }
            return fingerprint.toString().equals(args[0]);
        } catch (Exception invalidArtifact) {
            return false;
        }
    }

    @Override public UserDeviceInfo initialize(WorkloadInfo info) {
        if (info.getSd().end_lba != ZipfDistribution.CAPACITY
                || info.getAverageXferSize() != TRANSFER_BYTES || info.getRdPct() != 90) {
            throw new IllegalArgumentException("FAST requires a 4 TiB concat, 128 KiB transfer and rdpct=90");
        }
        long hash = 0xcbf29ce484222325L;
        String identity = getSlaveLabel() + "/" + info.getSd().sd_name;
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
