package idfsgraphzipf;

import User.*;
import idfscommon.RuntimeIdentity;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** Original Vdbench User adapter: native RD owns time/rate; callback selects each address. */
public final class ZipfWorkload extends UserClass {
    private static final ConcurrentHashMap<Long, ZipfDistribution> DISTRIBUTIONS = new ConcurrentHashMap<>();
    private long mappingSeed;
    private WorkloadInfo work;
    private ThreadLocal<ZipfDistribution.Stream> streams;

    @Override public boolean parser(String[] args) {
        if (args.length != 3 || !args[0].equals("zipf")) return false;
        try {
            if (!args[1].startsWith("seed")) return false;
            mappingSeed = Long.parseLong(args[1].substring(4));
            return RuntimeIdentity.matchesJar(ZipfWorkload.class, args[2]);
        } catch (IllegalArgumentException bad) { return false; }
    }

    private static long mix(long x) {
        x = (x ^ (x >>> 30)) * 0xbf58476d1ce4e5b9L;
        x = (x ^ (x >>> 27)) * 0x94d049bb133111ebL;
        return x ^ (x >>> 31);
    }

    private static String clientIdentity() {
        String explicit = System.getProperty("graph.zipf.client");
        if (explicit != null && !explicit.isEmpty()) return explicit;
        String host = System.getenv("HOSTNAME");
        if (host == null || host.isEmpty()) {
            try { host = InetAddress.getLocalHost().getHostName(); }
            catch (UnknownHostException bad) {
                throw new IllegalArgumentException("cannot identify client; set graph.zipf.client", bad);
            }
        }
        return host + "/" + String.valueOf(UserClass.getSlaveLabel());
    }

    @Override public UserDeviceInfo initialize(WorkloadInfo info) {
        if (info.getSd().end_lba != ZipfDistribution.CAPACITY || info.getAverageXferSize() != 32)
            throw new IllegalArgumentException("graph Zipf requires the full4TiB extent and32B buffer");
        final ZipfDistribution distribution = DISTRIBUTIONS.computeIfAbsent(mappingSeed, ZipfDistribution::new);
        String identity = clientIdentity() + "/" + info.getSd().sd_name;
        long hash = 0xcbf29ce484222325L;
        for (int i = 0; i < identity.length(); i++) hash = (hash ^ identity.charAt(i)) * 0x100000001b3L;
        final long base = mix(mappingSeed ^ hash);
        final AtomicLong sequence = new AtomicLong();
        streams = ThreadLocal.withInitial(() -> distribution.stream(mix(base + sequence.getAndIncrement())));
        work = info;
        return super.initialize(info);
    }

    @Override public boolean generate() {
        if (work == null) throw new IllegalStateException("not initialized");
        work.setFirstStartDelta(0);
        long generated = 0;
        while (!isWorkloadDone()) {
            if (!work.scheduleIO(0, 0, 32, true)) break;
            generated++;
        }
        work.setEOF(generated);
        return generated > 0;
    }

    @Override public boolean preIO(UserCmd command) {
        if (streams == null) throw new IllegalStateException("not initialized");
        command.setLba(streams.get().nextOffset());
        command.setXfersize(32); command.setReadFlag(true);
        return true;
    }
    @Override public boolean postIO(UserCmd command) { return true; }
    @Override public void passData(String data) { }
}
