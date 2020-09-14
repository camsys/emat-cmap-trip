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
Then, you might need to edit the paths in the file `cmap-trip-model-config.yml`:

    model_path: The relative path from this directory to where the base core model is stored
    model_path_land_use_alt1: The relative path from where the base core model is stored to where the alt land use core model is stored
    model_archive: The relative path from this directory to where archived model results will be stored

Then, interact with EMAT using these Jupyter notebooks:

- To re-initialize the results database:

    `conda activate CMAP-EMAT && jupyter-notebook database-setup.ipynb`

- To run core model experiments:

    `conda activate CMAP-EMAT && jupyter-notebook running-experiments.ipynb`
    
- To run univariate sensitivity tests:

    `conda activate CMAP-EMAT && jupyter-notebook univariate-sensitivity-tests.ipynb`

