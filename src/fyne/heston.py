import cmath
from itertools import repeat
from math import pi
from timeit import Timer

import numpy as np
from numba import njit
from scipy.integrate import quad
from scipy.optimize import leastsq

from fyne.common import _lipton_integrand


def formula(underlying_price, strike, expiry, vol, kappa, theta, nu, rho):
    r"""Heston formula

    Computes the price of the option according to the Heston formula.

    Parameters
    ----------
    underlying_price : float
        Price of the underlying asset.
    strike : float
        Strike of the option.
    expiry : float
        Time remaining until the expiry of the option.
    vol : float
        Instantaneous volatility.
    kappa : float
        Model parameter :math:`\kappa`.
    theta : float
        Model parameter :math:`\theta`.
    nu : float
        Model parameter :math:`\nu`.
    rho : float
        Model parameter :math:`\rho`.

    Returns
    -------
    float
        Option price according to Heston formula.

    Example
    -------

    >>> from fyne import heston
    >>> v, kappa, theta, nu, rho = 0.2, 1.3, 0.04, 0.4, -0.3
    >>> underlying_price = 100.
    >>> strike = 90.
    >>> maturity = 0.5
    >>> option_price = heston.formula(underlying_price, strike, maturity,
    ...                               v, kappa, theta, nu, rho)
    >>> round(option_price, 2)
    16.32

    """

    k = np.log(strike/underlying_price)
    a = kappa*theta
    return _reduced_formula(k, expiry, vol, kappa, a, nu, rho)*underlying_price


def calibration(underlying_price, strike, expiry, option_prices, initial_guess):
    r"""Heston calibration

    Recovers the Heston model parameters from options implied volatilities. The
    calibration is performed using the Levenberg-Marquardt algorithm.

    Parameters
    ----------
    underlying_price : float
        Price of the underlying asset.
    strike : float
        Strike of the option.
    expiry : float
        Time remaining until the expiry of the option.
    implied_vols : float
        Implied volatilities of call options.
    initial_guess : float, optional
        Initial guess for the implied volatility for the Newton's method.

    Returns
    -------
    tuple of floats
        Returns the calibrated :math:`V_0`, :math:`\kappa`, :math:`\theta`,
        :math:`\nu` and :math:`\rho`, respectively.

    Example
    -------

    >>> import numpy as np
    >>> from fyne import heston
    >>> vol, kappa, theta, nu, rho = 0.0457, 5.07, 0.0457, 0.48, -0.767
    >>> underlying_price = 1640.
    >>> strikes = np.array([1312., 1312., 1640., 1640., 1968., 1968.])
    >>> expiries = np.array([0.25, 0.5, 0.25, 0.5, 0.25, 0.5])
    >>> option_prices = np.array(
    ...     [heston.formula(underlying_price, strike, expiry, vol, kappa,
    ...                     theta, nu, rho)
    ...      for strike, expiry in zip(strikes, expiries)])
    >>> initial_guess = np.array([vol + 0.01, kappa + 1, theta + 0.01,
    ...                           nu - 0.1, rho - 0.1])
    >>> calibrated = heston.calibration(underlying_price, strikes, expiries,
    ...                                 option_prices, initial_guess)
    >>> [round(param, 4) for param in calibrated]
    [0.0457, 5.07, 0.0457, 0.48, -0.767]

    """
    cs = option_prices/underlying_price
    ks = np.log(strike/underlying_price)
    vol0, kappa0, theta0, nu0, rho0 = initial_guess
    params = np.array([vol0, kappa0, kappa0*theta0, nu0, rho0])

    vol, kappa, a, nu, rho = _reduced_calibration(cs, ks, expiry, params)

    return vol, kappa, a/kappa, nu, rho


def benchmark(n):
    """Benchmarking function for the Heston formula implementation

    This function computes the time elapsed to evaluate the Heston formula
    under some example parameters for a specific maturity and a given number of
    strikes. The bounds of the strikes is kept fixed.

    Parameters
    ----------
    n : int
        Number of strikes to be evaluated

    Returns
    -------
    tuple of float
        Returns the time spent performing the Heston formula computations and
    its standard deviation.

    """

    setup = """
import numpy as np

from fyne.heston import _reduced_formula


t, v, kappa, a, nu, rho = 0.5, 0.0457, 5.07, 0.2317, 0.48, -0.767
ks = np.linspace(np.log(0.8), np.log(1.2), {})

# First execution to trigger JIT
_reduced_formula(0., t, v, kappa, a, nu, rho)""".format(n)

    timer = Timer('[_reduced_formula(k, t, v, kappa, a, nu, rho) for k in ks]',
                  setup=setup)
    return min(map(lambda t: t.timeit(number=50)/50., repeat(timer, 5)))


@njit
def _heston_psi(u, t, kappa, a, nu, rho):
    d = cmath.sqrt(nu**2*(u**2 + 1j*u) + (kappa - 1j*nu*rho*u)**2)
    g = (-d + kappa - 1j*nu*rho*u)/(d + kappa - 1j*nu*rho*u)
    h = (g*cmath.exp(-d*t) - 1)/(g - 1)
    psi_1 = a*(t*(-d + kappa - 1j*nu*rho*u) - 2*cmath.log(h))/nu**2
    psi_2 = (1 - cmath.exp(-d*t))*(-d + kappa - 1j*nu*rho*u)/(
        (-g*cmath.exp(-d*t) + 1)*nu**2)
    return psi_1, psi_2


@njit
def _integrand(u, k, t, v, kappa, a, nu, rho):
    psi_1, psi_2 = _heston_psi(u - 0.5j, t, kappa, a, nu, rho)
    return _lipton_integrand(u, k, v, psi_1, psi_2)


def _reduced_formula(k, t, v, kappa, a, nu, rho):
    c = 1 - quad(lambda u: _integrand(u, k, t, v, kappa, a, nu, rho), 0,
                 np.inf)[0]/pi

    no_arb_low_bound = max(0., 1. - np.exp(k))
    no_arb_up_bound = 1.
    if c <= no_arb_low_bound:
        raise ValueError("Warning: Option price below no-arbitrage bounds")
    elif c >= no_arb_up_bound:
        raise ValueError("Warning: Option price above no-arbitrage bounds")

    return c


def _calibration_loss(cs, ks, ts, params):
    v, kappa, a, nu, rho = params
    cs_heston = np.array([_reduced_formula(k, t, v, kappa, a, nu, rho)
                          for k, t in zip(ks, ts)])
    return cs_heston - cs


def _reduced_calibration(cs, ks, ts, params):
    params, ier = leastsq(lambda params: _calibration_loss(cs, ks, ts, params),
                          params)

    if ier not in [1, 2, 3, 4]:
        raise ValueError("Heston calibration failed. ier = {}".format(ier))

    return params
