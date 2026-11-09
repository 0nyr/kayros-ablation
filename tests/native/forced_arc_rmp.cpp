// Synthetic SPF witness: restricted-master infeasibility needs further evidence.
#include <iostream>
#include "bcp/spf.h"
#include "goc/linear_programming/solver/lp_solver.h"
int main() {
    solver::SPF spf(4);
    spf.AddRoute(goc::Route({0,1,3}, 0., 2.));
    spf.AddRoute(goc::Route({0,2,3}, 0., 2.));
    goc::LPSolver lp;
    auto initial = lp.Solve(spf.formulation);
    // Force customer arc 1->2 by forbidding the incompatible singleton arcs.
    spf.SetForbiddenArcs({{1,3},{0,2}});
    auto restricted = lp.Solve(spf.formulation);
    // This admissible missing route restores feasibility without changing rows.
    spf.AddRoute(goc::Route({0,1,2,3}, 0., 3.));
    auto augmented = lp.Solve(spf.formulation);
    bool a = initial.status == goc::LPStatus::Optimum;
    bool b = restricted.status == goc::LPStatus::Infeasible;
    bool c = augmented.status == goc::LPStatus::Optimum;
    std::cout << "{\"initial_feasible\":" << a << ",\"restricted_infeasible\":" << b
              << ",\"missing_route_restores_feasibility\":" << c << "}\n";
    return a && b && c ? 0 : 1;
}
