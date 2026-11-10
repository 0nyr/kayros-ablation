#include "captured_pricing_replay.h"
#include <bitset>
#include <cassert>
#include <iostream>
#include <utility>

using nlohmann::json;
using kayros::lera::CapturedPricingReplay;
using kayros::lera::PricingTrace;

struct Problem {
    std::vector<double> P, sigma;
    std::vector<std::bitset<64>> S;
    std::vector<std::pair<int, int>> A;
};

struct Labeling {
    int merge_start = 0, solution_limit = 1;
    bool closing_state = true, partial = false, limited_extension = false;
    bool lazy_extension = false, unreachable_strengthened = false, sort_by_cost = false;
    bool correcting = true, symmetric = true;
};

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string path = std::string(argv[1]) + "/captured.json";
    const std::string trace_path = std::string(argv[1]) + "/captured-trace.jsonl";
    unsetenv("KAYROS_FIRST_PRICING_REPLAY");
    unsetenv("KAYROS_STEP_EXACT");
    unsetenv("KAYROS_CAPTURED_PRICING_REPLAY");
    assert(!CapturedPricingReplay(false));
    json fixture = {{"schema_version", 1}, {"source_call", 15}, {"level", "Exact"},
                    {"merge_start", 346}, {"closing_state", false}, {"solution_limit", 3000},
                    {"policy", {{"partial", true}, {"limited_extension", true}, {"lazy_extension", true},
                                {"unreachable_strengthened", true}, {"sort_by_cost", true},
                                {"correcting", false}, {"symmetric", false}, {"step_exact", false},
                                {"ng_routes_enabled", false}, {"monodirectional_process_limit", 10}}},
                    {"context", {{"profits", std::vector<double>(61, 1.0)},
                                 {"cuts", {{6, 31, 33}, {8, 16, 42}}},
                                 {"cuts_duals", {-5.171628250102685, -2.0551182657227187}},
                                 {"forbidden_arcs", {{1, 2}}}}}};
    std::ofstream(path) << fixture;
    setenv("KAYROS_CAPTURED_PRICING_REPLAY", path.c_str(), 1);
    CapturedPricingReplay replay(true);
    Problem problem;
    problem.P.resize(61, 0.0);
    replay.apply(problem);
    assert(problem.S.size() == 2 && problem.S[0].count() == 3);
    assert(problem.S[0][6] && problem.S[0][31] && problem.S[0][33]);
    assert(problem.sigma == fixture["context"]["cuts_duals"].get<std::vector<double>>());
    assert((problem.A == std::vector<std::pair<int, int>>({{1, 2}})));
    assert(replay.level_index() == 2 && replay.source_call() == 15);
    Labeling labeling;
    replay.configure(labeling);
    assert(labeling.merge_start == 346 && !labeling.closing_state && labeling.solution_limit == 3000);
    assert(!labeling.correcting && !labeling.symmetric && labeling.partial);
    bool rejected = false;
    try { replay.apply(problem); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    fixture["context"]["cuts"][0] = {6, 31, 31};
    std::ofstream(path) << fixture;
    CapturedPricingReplay invalid_cut(true);
    rejected = false;
    try { invalid_cut.apply(problem); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    setenv("KAYROS_STEP_EXACT", "1", 1);
    rejected = false;
    try { CapturedPricingReplay wrong_mode(true); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    unsetenv("KAYROS_STEP_EXACT");
    setenv("KAYROS_PRICING_TRACE", trace_path.c_str(), 1);
    {
        PricingTrace trace;
        rejected = false;
        try { replay.finish(trace); } catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
    json complete;
    std::ifstream(trace_path) >> complete;
    assert(complete["event"] == "captured_replay_complete" && complete["solver_claim"] == false);
    std::cout << "PASS captured cuts, duals, controls, mode guards and non-solver completion\n";
}
