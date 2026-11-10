#include <iostream>
#include <stdexcept>
#include <string>

#include "goc/math/pwl_function.h"

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::string name = argv[1];
    if (name != "disjoint-constant" && name != "empty-outer" && name != "valid-overlap") return 2;
    const bool expected_empty = name != "valid-overlap";
    const goc::PWLFunction outer = name == "empty-outer" ? goc::PWLFunction() :
        goc::PWLFunction::ConstantFunction(0.0, {0.0, 1.0});
    const goc::PWLFunction inner = goc::PWLFunction::ConstantFunction(
        name == "valid-overlap" ? 0.5 : 2.0, {0.0, 1.0});
    std::cout << std::boolalpha << "case=" << name << " outer_piece_count=" << outer.PieceCount()
              << " inner_piece_count=" << inner.PieceCount() << " expected_empty=" << expected_empty
              << std::endl;
    const auto composition = outer.Compose(inner);
    std::cout << "result_piece_count=" << composition.PieceCount() << " empty=" << composition.Empty()
              << " domain_empty=" << composition.Domain().Empty() << std::endl;
    if (expected_empty)
    {
        if (!composition.Empty() || !composition.Domain().Empty())
            throw std::runtime_error("Composition should have an empty domain");
    }
    else
    {
        if (composition.Empty() || composition.Domain().left != 0.0 || composition.Domain().right != 1.0)
            throw std::runtime_error("Composition should have domain [0,1]");
        for (double t : {0.0, 0.5, 1.0})
        {
            const double value = composition(t);
            std::cout << "t=" << t << " composition=" << value << std::endl;
            if (value != 0.0 || value != outer(inner(t)))
                throw std::runtime_error("Composition should equal f(g(t)) = 0");
        }
    }
    std::cout << "PASS" << std::endl;
    return 0;
}
