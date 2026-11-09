"""Consistent arm colors across incumbent and closure figures."""


def arm_order(arm, prefix=None):
    order = ['lera-exact', 'kayros-bpca-cplex', 'kayros-bpca-highs', 'kayros-heuristics', 'kayros-full-cplex']
    return order.index(arm), float(prefix or 0)


def arm_style(arm, prefix=None):
    if arm == 'kayros-full-cplex':
        styles = {
            15.: ('#006d2c', '-'), 60.: ('#31a354', '--'),
            180.: ('#74c476', '-.'), 600.: ('#238b45', ':'),
            1800.: ('#00441b', (0, (5, 2, 1, 2))),
        }
        color, linestyle = styles.get(float(prefix), ('#006d2c', '-'))
        return {'color': color, 'linestyle': linestyle, 'linewidth': 1.7}
    return {'color': {'lera-exact': '#222222', 'kayros-bpca-cplex': '#d95f02',
                      'kayros-bpca-highs': '#7570b3', 'kayros-heuristics': '#1f78b4'}[arm],
            'linestyle': '-', 'linewidth': 1.7}
