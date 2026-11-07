package idfsai;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.function.LongSupplier;

/** Pure sampler checks using real inventory metadata, without opening a data file. */
public final class AITests {
    public static final long G = 4194304L;
    public static final class Clock implements LongSupplier {
        public long now;
        public long getAsLong() { return now; }
    }
    public static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    private static final class Region {
        String id;
        int count, fragments, objectsPerFragment, objects;
        long base, slot, extent;
        int[] ranks;
        Region(String[] row) {
            id = row[1]; count = Integer.parseInt(row[2]);
            fragments = Integer.parseInt(row[4]); base = Long.parseLong(row[5]);
            slot = Long.parseLong(row[6]); extent = Long.parseLong(row[3]) / fragments;
            objectsPerFragment = (int)(slot / G); objects = count * fragments * objectsPerFragment;
            List<Integer> addresses = new ArrayList<Integer>();
            for (int i = 0; i < objects; i++) addresses.add(i);
            Collections.shuffle(addresses, new Random(Long.parseLong(row[7])));
            ranks = new int[objects];
            for (int rank = 0; rank < objects; rank++) ranks[addresses.get(rank)] = rank + 1;
        }
        long end() { return base + slot * count * fragments; }
    }
    private static void sample(Path path, boolean training, boolean zipf) throws Exception {
        List<String> rows = Files.readAllLines(path, StandardCharsets.UTF_8);
        List<Region> regions = new ArrayList<Region>();
        Map<String, Double> targetGroups = new HashMap<String, Double>();
        long capacity = Long.parseLong(rows.get(0).split("\t")[2]);
        for (String row : rows) {
            String[] p = row.split("\t");
            if (p[0].equals("G")) regions.add(new Region(p));
            if (p[0].equals("C")) targetGroups.put(p[1], targetGroups.getOrDefault(p[1], 0.) + Double.parseDouble(p[5]));
        }
        Timeline timeline = Timeline.load(path, 12345, zipf);
        Timeline.Sample s = new Timeline.Sample();
        Map<String, Integer> joint = new HashMap<String, Integer>();
        Map<String, Integer> groupCounts = new HashMap<String, Integer>();
        Map<String, Integer> hotCounts = new HashMap<String, Integer>();
        Map<String, Long> previousObject = new HashMap<String, Long>();
        Map<String, Integer> transitions = new HashMap<String, Integer>();
        Map<String, Integer> repeats = new HashMap<String, Integer>();
        Map<String, Integer> offsetCounts = new HashMap<String, Integer>();
        Map<String, Double> offsetFractions = new HashMap<String, Double>();
        int total = 600000;
        for (int i = 0; i < total; i++) {
            check(timeline.next(i * 999L, s), "600-second sampler stays active without a quota");
            Region region = null; int groupIndex = -1;
            for (int n = 0; n < regions.size(); n++)
                if (s.offset >= regions.get(n).base && s.offset < regions.get(n).end()) {
                    region = regions.get(n); groupIndex = n; break;
                }
            check(region != null && s.offset % 4096 == 0 && s.size % 4096 == 0 && s.size > 0,
                  "mapped aligned request belongs to an original file range");
            long within = s.offset - region.base;
            int physical = (int)(within / region.slot);
            int object = (int)(within / G);
            long objectOffset = within % G;
            long valid = Math.min(G, region.extent - (object % region.objectsPerFragment) * G);
            long offset = within % region.slot;
            check(offset + s.size <= region.extent && s.offset + s.size <= capacity,
                  "request cannot enter padding or another original fragment");
            check(objectOffset + s.size <= valid, "request stays inside one valid 4MiB object");
            boolean random = s.random;
            check(random, "all training/inference reads and writes use random offsets");
            String key = (s.read ? "read" : "write") + ":" + (random ? "random" : "sequential") + ":" + s.size;
            joint.put(key, joint.getOrDefault(key, 0) + 1);
            groupCounts.put(region.id, groupCounts.getOrDefault(region.id, 0) + 1);
            boolean hot = region.ranks[object] <= (region.objects + 4) / 5;
            if (hot)
                hotCounts.put(region.id, hotCounts.getOrDefault(region.id, 0) + 1);
            String channel = region.id + ":" + key;
            Long previous = previousObject.put(channel, s.offset / G);
            if (previous != null) {
                transitions.put(channel, transitions.getOrDefault(channel, 0) + 1);
                if (previous == s.offset / G)
                    repeats.put(channel, repeats.getOrDefault(channel, 0) + 1);
            }
            if (valid > s.size) {
                offsetCounts.put(channel, offsetCounts.getOrDefault(channel, 0) + 1);
                offsetFractions.put(channel, offsetFractions.getOrDefault(channel, 0.) +
                                    objectOffset / (double)(valid - s.size));
            }
        }
        Map<String, Double> expected = new HashMap<String, Double>();
        if (training) {
            expected.put("read:random:8192", .2); expected.put("read:random:32768", .2);
            expected.put("read:random:65536", .1); expected.put("write:random:65536", .1);
            expected.put("read:random:1048576", .2); expected.put("write:random:1048576", .2);
        } else {
            expected.put("write:random:65536", .1); expected.put("write:random:131072", .1);
            expected.put("read:random:65536", .4); expected.put("read:random:131072", .4);
        }
        check(joint.keySet().equals(expected.keySet()), "pure-random adaptation keeps SES operation/size shares");
        for (String channel : offsetCounts.keySet()) {
            int n = offsetCounts.get(channel);
            if (n > 1000)
                check(Math.abs(offsetFractions.get(channel) / n - .5) < .05,
                      "uniform legal offsets across the selected object: " + channel);
        }
        for (String key : expected.keySet())
            check(Math.abs(joint.get(key) / (double)total - expected.get(key)) < .004,
                  "sampled SES joint request shares: " + key);
        for (Region r : regions) {
            int n = groupCounts.getOrDefault(r.id, 0);
            check(Math.abs(n / (double)total - targetGroups.get(r.id)) < .004, "size-class request shares");
            double z = 0, top = 0, squared = 0;
            for (int rank = 1; rank <= r.objects; rank++) {
                double weight = zipf ? Math.pow(rank, -.99) : 1;
                z += weight; squared += weight * weight;
                if (rank <= (r.objects + 4) / 5) top += weight;
            }
            if (n > 5000)
                check(Math.abs(hotCounts.getOrDefault(r.id, 0) / (double)n - top / z) < .02,
                      "object top20 probability uses analytic Zipf or uniform baseline");
            for (String channel : transitions.keySet()) if (channel.startsWith(r.id + ":")) {
                int nTransitions = transitions.get(channel);
                if (nTransitions < 5000) continue;
                double repeatProbability = squared / (z * z);
                double observed = repeats.getOrDefault(channel, 0) / (double)nTransitions;
                // IID draws with replacement repeat with probability sum(p_i^2).
                // Four-I/O holding repeats far too often; Weyl/without-replacement
                // choices repeat too rarely. Broad fixed-seed statistical margin.
                double tolerance = .001 + 8 * Math.sqrt(repeatProbability *
                    (1 - repeatProbability) / nTransitions);
                check(Math.abs(observed - repeatProbability) < tolerance,
                      "each I/O independently reselects with replacement: " + channel +
                      " observed=" + observed + " expected=" + repeatProbability);
            }
        }
        check(!timeline.next(600000000, s), "exact 600s end");
        timeline.finalSample(s);
        check(s.offset >= 0 && s.offset + s.size <= capacity, "late queued token remains a valid request");
        try { timeline.next(599999999, s); throw new AssertionError("reversed clock accepted"); }
        catch (IllegalArgumentException expectedError) { }
        System.out.println("AI_SAMPLE " + (training ? "training" : "inference") + " " + (zipf ? "zipf" : "uniform")
                           + " requests=" + total + " joint=" + joint);
    }
    public static void main(String[] args) throws Exception {
        for (int c = 0; c < args.length; c++) {
            Path path = Paths.get(args[c]);
            sample(path, c == 0, true); sample(path, c == 0, false);
            Timeline dense = Timeline.load(path, 71, true), sparse = Timeline.load(path, 71, true);
            Timeline.Sample a = new Timeline.Sample(), b = new Timeline.Sample();
            for (int i = 0; i < 50000; i++) {
                dense.next(i * 10L, a); sparse.next(i * 11000L, b);
                check(a.offset == b.offset && a.size == b.size && a.read == b.read && a.random == b.random,
                      "static SES-derived law and RNG streams do not reset or change with request rate");
            }
            String text = new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
            Path bad = path.resolveSibling("invalid.timeline");
            String[] invalid = {text.replace("\t0.99\t", "\t0.5\t"),
                                text.replace("\t1\tiid\n", "\t4\tiid\n"),
                                text.replace("\t1\tiid\n", "\t1\tweyl\n"),
                                text.replace("AI_SES_OBJECT3", "AI_SES_OBJECT2"),
                                text.replace("AI_SES_OBJECT3", "AI_SES_OBJECT1"),
                                text.replaceFirst("C\\t", "Q\t"),
                                text.replaceFirst("\\tread\\t", "\ttrim\t"),
                                text.replaceFirst("\\trandom\\t", "\tsequential\t"),
                                text.replaceFirst("\\trandom\\t", "\tunknown\t"),
                                text + "G\tduplicate\t1\t65536\t1\t0\t4194304\t20261021\n"};
            for (String value : invalid) {
                Files.write(bad, value.getBytes(StandardCharsets.UTF_8));
                try { Timeline.load(bad, 7, true); throw new AssertionError("invalid timeline accepted"); }
                catch (IOException expected) { }
            }
        }
        System.out.println("AI_SAMPLER_TESTS_PASS exclusive_objects SES_read_write_size_mix object_Zipf uniform_control iid_per_io random_offsets no_quota no_physical_io=true");
    }
}
