# emat-cmap-trip

EMAT Implementation for the CMAP Trip-based Model

## Installation

To use these tools, you must first install [Anaconda Python](https://www.anaconda.com/products/individual).

Once Anaconda is installed, you can create an *environment* to work in by launching the 
*Anaconda Prmopt* from the Windows Start menu, navigating to this directory, and running::

    conda env create -f conda-environment.yml
    
This will download and install the required dependencies for TMIP-EMAT.

If you will be running the actual CMAP Trip-based model (instead of merely exploring the outputs)
you will also need to install Emme and the core model itself (instructions provided separately).



- To re-initialize the results database:

    `conda activate CMAP-EMAT && jupyter-notebook database-setup.ipynb`

- To run core model experiments:

    `conda activate CMAP-EMAT && jupyter-notebook running-experiments.ipynb`
    
- To run univariate sensitivity tests:

    `conda activate CMAP-EMAT && jupyter-notebook univariate-sensitivity-tests.ipynb`

