// Offline metadata scan/filter. Never opens a replay data file or runs Vdbench.
#include <algorithm>
#include <charconv>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>
#include <zlib.h>

using U = std::uint64_t;
struct KeyData { U end = 0, reads = 0, writes = 0; };
struct Inventory { U key, end, reads, writes; };
struct Selected { U key, end, reads, writes, base, slot, hash; };
#pragma pack(push, 1)
struct Event { U source_row, time_ns, offset; std::uint32_t size, op, key_index; };
#pragma pack(pop)
static_assert(sizeof(Inventory) == 32 && sizeof(Selected) == 56 && sizeof(Event) == 36);

U number(const std::string& token) {
    U n = 0; auto r = std::from_chars(token.data(), token.data() + token.size(), n);
    if (r.ec != std::errc() || r.ptr != token.data() + token.size())
        throw std::runtime_error("invalid unsigned integer: " + token);
    return n;
}
U timestamp(const std::string& s) {
    const auto dot = s.find('.');
    if (dot == std::string::npos) return number(s) * 1000000000ULL;
    std::string fraction = s.substr(dot + 1);
    if (fraction.empty() || fraction.size() > 9) throw std::runtime_error("timestamp precision exceeds nanoseconds");
    fraction.append(9 - fraction.size(), '0');
    return number(s.substr(0, dot)) * 1000000000ULL + number(fraction);
}
std::vector<std::string> fields(const std::string& s) {
    std::vector<std::string> result; result.reserve(8);
    std::size_t p = 0;
    while (p < s.size()) {
        while (p < s.size() && (s[p] == ' ' || s[p] == '\t' || s[p] == '\r')) ++p;
        auto begin = p;
        while (p < s.size() && s[p] != ' ' && s[p] != '\t' && s[p] != '\r') ++p;
        if (p > begin) result.emplace_back(s.substr(begin, p - begin));
    }
    if (result.size() != 8) throw std::runtime_error("Region2 requires eight source columns");
    return result;
}

int main(int argc, char** argv) try {
    if (argc < 3) throw std::runtime_error("scan_region2 inventory OUT or filter SELECTED OUT_PREFIX");
    std::ios::sync_with_stdio(false); std::cin.tie(nullptr);
    std::string mode = argv[1], line;
    U rows = 0, reads = 0, writes = 0, low = std::numeric_limits<U>::max(), high = 0;
    U previous = 0, reversals = 0;
    if (mode == "inventory") {
        std::unordered_map<U, KeyData> keys; keys.reserve(8500000);
        while (std::getline(std::cin, line)) {
            if (line.empty() || line.front() == '#') continue;
            const auto f = fields(line);
            U key = number(f[0]), offset = number(f[1]), size = number(f[2]), when = timestamp(f[3]), op = number(f[4]);
            if (!size || op < 1 || op > 6 || offset > std::numeric_limits<U>::max() - size)
                throw std::runtime_error("invalid native data request");
            ++rows; if (rows > 1 && when < previous) ++reversals; previous = when;
            low = std::min(low, when); high = std::max(high, when);
            auto& k = keys[key]; k.end = std::max(k.end, offset + size);
            if (op == 1 || op == 2 || op == 5) { ++reads; ++k.reads; }
            else { ++writes; ++k.writes; }
            if (rows % 10000000 == 0) std::cerr << "inventory rows=" << rows << " keys=" << keys.size() << '\n';
        }
        std::vector<Inventory> inventory; inventory.reserve(keys.size());
        for (const auto& [key, k] : keys) inventory.push_back({key, k.end, k.reads, k.writes});
        std::sort(inventory.begin(), inventory.end(), [](const auto& a, const auto& b){ return a.key < b.key; });
        std::ofstream out(argv[2], std::ios::binary); out.exceptions(std::ios::failbit | std::ios::badbit);
        out.write(reinterpret_cast<const char*>(inventory.data()), inventory.size() * sizeof(Inventory)); out.close();
        U extent = 0, slots = 0;
        for (const auto& k : inventory) { extent += k.end; slots += (k.end + 4194303) / 4194304; }
        std::cout << "{\"rows\":" << rows << ",\"keys\":" << inventory.size()
                  << ",\"reads\":" << reads << ",\"writes\":" << writes
                  << ",\"time_min_ns\":" << low << ",\"time_max_ns\":" << high
                  << ",\"timestamp_decreases\":" << reversals << ",\"extent_bytes\":" << extent
                  << ",\"inventory_objects\":" << slots << "}\n";
    } else if (mode == "filter" && argc == 6) {
        const U clock_start = number(argv[4]), clock_end = number(argv[5]);
        if (clock_end <= clock_start) throw std::runtime_error("bad full source clock");
        std::vector<U> full_requests(600), full_reads(600), full_bytes(600);
        std::ifstream source(argv[2], std::ios::binary | std::ios::ate);
        const auto length = source.tellg();
        if (length <= 0 || length % sizeof(Selected)) throw std::runtime_error("bad selected inventory");
        std::vector<Selected> selected(length / sizeof(Selected)); source.seekg(0);
        source.read(reinterpret_cast<char*>(selected.data()), length);
        if (!source) throw std::runtime_error("incomplete selected inventory");
        std::unordered_map<U, std::uint32_t> lookup; lookup.reserve(selected.size() * 2);
        for (std::size_t i = 0; i < selected.size(); ++i) {
            if (!lookup.emplace(selected[i].key, i).second) throw std::runtime_error("duplicate selected key");
        }
        std::ofstream events(std::string(argv[3]) + ".events", std::ios::binary);
        events.exceptions(std::ios::failbit | std::ios::badbit);
        gzFile text = gzopen((std::string(argv[3]) + ".trace.gz").c_str(), "wb6");
        if (!text) throw std::runtime_error("cannot write selected trace");
        gzbuffer(text, 1048576);
        const std::string header = "# block_id io_offset io_size op_time op_name pipeline user_namespace user_name\n";
        if (gzwrite(text, header.data(), header.size()) != static_cast<int>(header.size())) throw std::runtime_error("gzip header write failed");
        U kept = 0, max_size = 0;
        std::vector<U> actual_reads(selected.size()), actual_writes(selected.size());
        while (std::getline(std::cin, line)) {
            if (line.empty() || line.front() == '#') continue;
            ++rows;
            const auto f = fields(line);
            U when = timestamp(f[3]), op = number(f[4]), size = number(f[2]);
            if (when < clock_start || when > clock_end) throw std::runtime_error("event outside full source clock");
            const auto second = std::min<U>(599, (when - clock_start) * 600 / (clock_end - clock_start));
            ++full_requests[second]; full_bytes[second] += size;
            if (op == 1 || op == 2 || op == 5) ++full_reads[second];
            const auto found = lookup.find(number(f[0]));
            if (found == lookup.end()) continue;
            auto index = found->second;
            U offset = number(f[1]);
            if (!size || size > std::numeric_limits<std::uint32_t>::max() || op < 1 || op > 6 || offset + size > selected[index].end)
                throw std::runtime_error("selected request escapes native key extent");
            if (kept && when < previous) ++reversals;
            previous = when;
            low = std::min(low, when); high = std::max(high, when); max_size = std::max(max_size, size);
            Event e{rows, when, offset, static_cast<std::uint32_t>(size), static_cast<std::uint32_t>(op), index};
            events.write(reinterpret_cast<const char*>(&e), sizeof(e));
            line.push_back('\n');
            if (gzwrite(text, line.data(), line.size()) != static_cast<int>(line.size())) throw std::runtime_error("gzip request write failed");
            if (op == 1 || op == 2 || op == 5) { ++reads; ++actual_reads[index]; }
            else { ++writes; ++actual_writes[index]; }
            ++kept;
        }
        events.close(); if (gzclose(text) != Z_OK) throw std::runtime_error("gzip close failed");
        for (std::size_t i = 0; i < selected.size(); ++i)
            if (actual_reads[i] != selected[i].reads || actual_writes[i] != selected[i].writes)
                throw std::runtime_error("not all source records for a selected key were retained");
        std::cout << "{\"source_rows\":" << rows << ",\"selected_rows\":" << kept
                  << ",\"reads\":" << reads << ",\"writes\":" << writes
                  << ",\"selected_time_min_ns\":" << low << ",\"selected_time_max_ns\":" << high
                  << ",\"timestamp_decreases\":" << reversals << ",\"maximum_raw_request\":" << max_size
                  << ",\"all_records_for_each_selected_key_retained\":true";
        auto array = [](const char* name, const std::vector<U>& values) {
            std::cout << ",\"" << name << "\":[";
            for (std::size_t i = 0; i < values.size(); ++i) {
                if (i) std::cout << ',';
                std::cout << values[i];
            }
            std::cout << ']';
        };
        array("full_requests_per_slice", full_requests);
        array("full_reads_per_slice", full_reads);
        array("full_bytes_per_slice", full_bytes);
        std::cout << "}\n";
    } else throw std::runtime_error("unknown mode");
    return 0;
} catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
