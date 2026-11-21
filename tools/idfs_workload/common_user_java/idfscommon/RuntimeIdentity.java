package idfscommon;

import User.UserClass;
import User.WorkloadInfo;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;

/** Tie each slave to the controller's User artifact and give each client a sampling stream. */
public final class RuntimeIdentity {
    private RuntimeIdentity() { }

    public static boolean matchesJar(Class<?> owner, String fingerprint) {
        if (!fingerprint.matches("jarsha[0-9a-f]{64}")) return false;
        try {
            Path jar = Paths.get(owner.getProtectionDomain().getCodeSource().getLocation().toURI());
            if (!Files.isRegularFile(jar)) return false;
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(jar));
            StringBuilder actual = new StringBuilder("jarsha");
            for (byte value : digest) {
                actual.append(Character.forDigit((value >>> 4) & 15, 16));
                actual.append(Character.forDigit(value & 15, 16));
            }
            return actual.toString().equals(fingerprint);
        } catch (Exception invalidArtifact) {
            return false;
        }
    }

    public static long samplingSeed(long seed, WorkloadInfo info) {
        if (UserClass.getSlaveCount() <= 1) return seed;
        String device = info.getSd().sd_name;
        long hash = 0xcbf29ce484222325L;
        for (int i = 0; i < device.length(); i++) {
            hash = (hash ^ device.charAt(i)) * 0x100000001b3L;
        }
        return seed ^ hash;
    }
}
