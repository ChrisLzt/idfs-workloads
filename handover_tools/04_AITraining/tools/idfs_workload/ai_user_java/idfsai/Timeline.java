package idfsai;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** SES-derived stationary shares; IID object and random-offset choices across 1s slices. */
public final class Timeline {
    private static final long G = 4194304L, DURATION = 600000000L, SLICE = 1000000L;
    private static final long MAX_CAPACITY = 6L * 1024 * 1024 * 1024 * 1024;
    public static final class Sample {
        public long offset;
        public int size;
        public boolean read, random;
    }
    private static final class Group {
        final int objectsPerFragment, totalObjects;
        final long base, extent, slot;
        final double[] cdf;
        final int[] objects;
        Group(int count, int fragments, long rankSeed, long base, long extent, long slot, boolean zipf) {
            this.base = base; this.extent = extent; this.slot = slot;
            objectsPerFragment = (int)(slot / G);
            totalObjects = count * fragments * objectsPerFragment;
            cdf = zipf ? new double[totalObjects] : null;
            objects = new int[totalObjects];
            double sum = 0;
            for (int r = 0; r < totalObjects; r++) {
                objects[r] = r;
                if (zipf) { sum += Math.pow(r + 1, -.99); cdf[r] = sum; }
            }
            // Rank placement is fixed independently of run seed, rate and time.
            Random permutation = new Random(rankSeed);
            for (int i = objects.length - 1; i > 0; i--) {
                int j = permutation.nextInt(i + 1), value = objects[i];
                objects[i] = objects[j]; objects[j] = value;
            }
        }
        int selectAt(double fraction) {
            int rank;
            if (cdf == null) rank = (int)(fraction * totalObjects);
            else {
                double u = fraction * cdf[cdf.length - 1];
                int lo = 0, hi = totalObjects - 1;
                while (lo < hi) { int mid = (lo + hi) >>> 1; if (cdf[mid] <= u) lo = mid + 1; else hi = mid; }
                rank = lo;
            }
            return objects[rank];
        }
        long valid(int object) { return Math.min(G, extent - (object % objectsPerFragment) * G); }
    }
    private static final class Channel {
        final Group group;
        final boolean read;
        final int size;
        final double end;
        private final Random objectRng;
        Channel(Group group, boolean read, int size, double end, long seed) {
            this.group = group; this.read = read; this.size = size; this.end = end;
            // Object draws must not consume the request/offset RNG. Domain-separate
            // each channel's repeatable stream from that run-level schedule.
            objectRng = new Random(seed ^ 0x9e3779b97f4a7c15L ^ group.base ^ ((long)size << 20) ^
                (read ? 0x8192L : 0x32768L) ^ 0x65536L);
        }
        int choose() {
            return group.selectAt(objectRng.nextDouble());
        }
        void sample(Random rng, Sample result) {
            int object = choose();
            long offset = (long)rng.nextInt((int)((group.valid(object) - size) / 4096 + 1)) * 4096;
            result.offset = group.base + object * G + offset;
            result.size = size; result.read = read; result.random = true;
        }
    }
    private final long capacity;
    private final int maximum;
    private final List<Channel> channels;
    private final double total;
    private final Random rng;
    private long last = -1;
    private Timeline(long capacity, int maximum, List<Channel> channels, double total, long seed) {
        this.capacity = capacity; this.maximum = maximum; this.channels = channels; this.total = total;
        this.rng = new Random(seed);
    }
    public static Timeline load(Path path, long seed, boolean zipf) throws IOException {
        List<String> rows = Files.readAllLines(path, StandardCharsets.UTF_8);
        try {
            if (rows.isEmpty()) throw new IOException("Empty SES timeline");
            String[] h = rows.get(0).split("\t", -1);
            if (h.length != 10 || !h[0].equals("AI_SES_OBJECT3") ||
                !(h[1].equals("ai_training") || h[1].equals("ai_inference")) ||
                Long.parseLong(h[3]) != G || Long.parseLong(h[4]) != DURATION ||
                Long.parseLong(h[5]) != SLICE || Double.parseDouble(h[7]) != .99 ||
                Integer.parseInt(h[8]) != 1 || !h[9].equals("iid"))
                throw new IOException("SES random adaptation requires version3,4MiB layout,600s,1s slices,object Zipf0.99 and per-I/O IID reselection");
            boolean training = h[1].equals("ai_training");
            long capacity = Long.parseLong(h[2]); int maximum = Integer.parseInt(h[6]);
            if (capacity <= 0 || capacity % G != 0 || capacity > MAX_CAPACITY ||
                maximum != (training ? 1048576 : 131072))
                throw new IOException("Invalid SES capacity (maximum6TiB) or transfer maximum");
            Set<Long> sizes = new HashSet<Long>(Arrays.asList(training ?
                new Long[]{65536L, 131072L, 1048576L, 2097152L, 8388608L, 134217728L, 1073741824L} :
                new Long[]{536870912L, 1073741824L, 2147483648L}));
            Map<String, Group> groups = new LinkedHashMap<String, Group>();
            List<Channel> channels = new ArrayList<Channel>(); Set<String> identities = new HashSet<String>();
            Map<String, Double> joint = new HashMap<String, Double>();
            long end = 0, physicalFiles = 0; double total = 0;
            for (int line = 1; line < rows.size(); line++) {
                String[] p = rows.get(line).split("\t", -1);
                if (p[0].equals("G")) {
                    if (p.length != 8 || !channels.isEmpty() || groups.containsKey(p[1]))
                        throw new IOException("Invalid or late SES group row");
                    int count = Integer.parseInt(p[2]), fragments = Integer.parseInt(p[4]);
                    long logicalSize = Long.parseLong(p[3]), base = Long.parseLong(p[5]), slot = Long.parseLong(p[6]);
                    long rankSeed = Long.parseLong(p[7]);
                    if (count <= 0 || count > 1000000 || fragments != (training ? 1 : 4) ||
                        !p[1].equals("size_" + logicalSize) || !sizes.remove(logicalSize) ||
                        base != end || base % G != 0 || rankSeed != (1048596L ^ logicalSize))
                        throw new IOException("Invalid SES file inventory or rank permutation");
                    long extent = logicalSize / fragments;
                    if (slot != ((extent - 1) / G + 1) * G || slot > capacity - base ||
                        (long)count * fragments > (capacity - base) / slot ||
                        physicalFiles + (long)count * fragments > 1000000)
                        throw new IOException("SES file slots exceed the container");
                    groups.put(p[1], new Group(count, fragments, rankSeed, base, extent, slot, zipf));
                    end = base + (long)count * fragments * slot; physicalFiles += (long)count * fragments;
                } else if (p[0].equals("C")) {
                    if (p.length != 6 || !groups.containsKey(p[1]) ||
                        !(p[2].equals("read") || p[2].equals("write")) ||
                        !p[3].equals("random"))
                        throw new IOException("Invalid SES channel row");
                    Group group = groups.get(p[1]); boolean read = p[2].equals("read");
                    int size = Integer.parseInt(p[4]); double weight = Double.parseDouble(p[5]);
                    boolean legalTransfer = training ?
                        (group.extent <= 131072 ? (size == 8192 || size == 32768 || size == 65536) && (read || size == 65536) : size == 1048576) :
                        size == 65536 || size == 131072;
                    if (!legalTransfer || size > maximum || group.extent % size != 0 ||
                        !(weight > 0) || Double.isInfinite(weight) ||
                        !identities.add(p[1] + ":" + p[2] + ":" + p[3] + ":" + size))
                        throw new IOException("Illegal SES request type, duplicate channel or probability");
                    total += weight;
                    if (!Double.isFinite(total)) throw new IOException("SES probability overflow");
                    channels.add(new Channel(group, read, size, total, seed));
                    String key = p[2] + ":" + p[3] + ":" + size;
                    joint.put(key, joint.getOrDefault(key, 0.) + weight);
                } else throw new IOException("Unknown SES timeline row");
            }
            if (end != capacity || !sizes.isEmpty() || channels.size() != (training ? 18 : 12) || Math.abs(total - 1) > 1e-9)
                throw new IOException("Incomplete SES inventory or request distribution");
            Map<String, Double> expected = new HashMap<String, Double>();
            if (training) {
                expected.put("read:random:8192", .2); expected.put("read:random:32768", .2);
                expected.put("read:random:65536", .1); expected.put("write:random:65536", .1);
                expected.put("read:random:1048576", .2); expected.put("write:random:1048576", .2);
            } else {
                expected.put("write:random:65536", .1); expected.put("write:random:131072", .1);
                expected.put("read:random:65536", .4); expected.put("read:random:131072", .4);
            }
            if (!joint.keySet().equals(expected.keySet())) throw new IOException("Unexpected SES operation/size combination");
            for (String key : expected.keySet())
                if (Math.abs(joint.get(key) - expected.get(key)) > 1e-9) throw new IOException("SES request shares changed");
            return new Timeline(capacity, maximum, channels, total, seed);
        } catch (IllegalArgumentException bad) { throw new IOException("Invalid SES timeline", bad); }
    }
    public long capacity() { return capacity; }
    public long duration() { return DURATION; }
    public int maximum() { return maximum; }
    private void sample(Sample result) {
        double u = rng.nextDouble() * total;
        for (Channel c : channels) if (u < c.end) { c.sample(rng, result); return; }
        throw new IllegalStateException("SES channel CDF has a gap");
    }
    public synchronized boolean next(long elapsedUs, Sample result) {
        if (elapsedUs < 0 || elapsedUs < last) throw new IllegalArgumentException("Elapsed clock reversed");
        last = elapsedUs;
        // All 600 one-second slices share this storage template. Neither object
        // selection streams nor offset RNG restart when the slice changes.
        if (elapsedUs / SLICE >= DURATION / SLICE) return false;
        sample(result); return true;
    }
    /** Stock preIO cannot cancel an already-in-flight command at shutdown. */
    public synchronized void finalSample(Sample result) { sample(result); }
}
