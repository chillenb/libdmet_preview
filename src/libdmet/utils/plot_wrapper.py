import functools

def plot_stixfont(func):
    """
    Wrap a plotting function to set rcParams so that plots use the STIX font.
    """
    @functools.wraps(func)
    def wrapper_decorator(*args, **kwargs):
        from matplotlib import rcParams
        _old_rcParams = rcParams.copy()

        rcParamsInput = kwargs.pop("rcParams", None)

        if rcParamsInput is None:
            # set defaults
            matplotlib.rcParams['mathtext.fontset'] = 'stix'
            matplotlib.rcParams['font.family'] = 'STIXGeneral'
            # set font to 42 for Type2 fonts:
            #matplotlib.rcParams['pdf.fonttype'] = 42
            #matplotlib.rcParams['ps.fonttype'] = 42
        else:
            # use user input rcParams
            rcParams.update(rcParamsInput)

        value = func(*args, **kwargs)

        # reset rcParams
        for key in rcParams:
            if rcParams[key] != _old_rcParams[key]:
                rcParams[key] = _old_rcParams[key]

        return value
    return wrapper_decorator