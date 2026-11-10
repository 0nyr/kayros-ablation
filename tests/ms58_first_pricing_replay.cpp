// Small standalone contract test for the shared replay header.
#include "pricing_trace.h"
#include <cassert>
#include <fstream>
#include <iostream>

using kayros::lera::FirstPricingReplay;
using kayros::lera::PricingTrace;
using nlohmann::json;

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string root = argv[1];
    const std::string fixture_path = root + "/fixture.json";
    const std::string trace_path = root + "/trace.jsonl";
    unsetenv("KAYROS_FIRST_PRICING_REPLAY");
    unsetenv("KAYROS_PRICING_TRACE");
    assert(!FirstPricingReplay(false));
    const json context = {{"profits", {0.0, 1141.5006037515082, 0.0}},
                          {"forbidden_arcs", json::array()},
                          {"cuts", json::array()}, {"cuts_duals", json::array()}};
    std::ofstream(fixture_path) << json{{"schema_version", 1}, {"context", context}};
    setenv("KAYROS_FIRST_PRICING_REPLAY", fixture_path.c_str(), 1);
    bool rejected = false;
    try { FirstPricingReplay replay(false); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    FirstPricingReplay replay(true);
    std::vector<double> profits{0.0, 1141.5006037515086, 0.0};
    replay.apply(context, profits);
    assert(profits == context.at("profits").get<std::vector<double>>());
    rejected = false;
    try { replay.apply(context, profits); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    FirstPricingReplay wrong_count(true);
    std::vector<double> short_profits(2, 0.0);
    rejected = false;
    try { wrong_count.apply(context, short_profits); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    FirstPricingReplay constrained(true);
    json wrong_context = context;
    wrong_context["forbidden_arcs"] = {{1, 2}};
    rejected = false;
    try { constrained.apply(wrong_context, profits); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    setenv("KAYROS_PRICING_TRACE", trace_path.c_str(), 1);
    {
        PricingTrace trace;
        rejected = false;
        try { replay.finish(trace); }
        catch (const std::runtime_error& error) {
            rejected = std::string(error.what()).find("MS58_FIRST_PRICING_REPLAY_COMPLETE") == 0;
        }
        assert(rejected);
    }
    json final_record;
    std::ifstream(trace_path) >> final_record;
    assert(final_record.at("event") == "replay_complete");
    assert(final_record.at("solver_claim") == false);
    assert(final_record.at("correcting") == false);
    std::cout << "PASS replay dual, context, single-call and intentional-stop contracts\n";
}
