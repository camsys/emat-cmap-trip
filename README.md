# emat-cmap-trip

EMAT Implementation for the CMAP Trip-based Model

## Installation

To use these tools, you must first install [Anaconda Python](https://www.anaconda.com/products/individual).
You'll also need to make this repository available on your local machine, either by
cloning it using *git*, or simply by downloading it directly from GitHub (look for
the green "Code" button with the dropdown menu, and choose "Download Zip").

Once Anaconda is installed and you have the repository locally, you can create a conda 
*environment* to work in by launching the *Anaconda Prompt* from the Windows Start menu, navigating 
to the top-level directory within this repository on your local machine, and running:

    conda env create -f conda-environment.yml
    
This will download and install the required dependencies for TMIP-EMAT.

If you will be running the actual CMAP Trip-based model (instead of merely exploring the outputs)
you will also need to install Emme and the core model itself (instructions provided separately,
as the former is proprietary software and the latter is a massive set of files not published in this
repository).  Then, you might need to edit the paths in the file `cmap-trip-model-config.yml` to
correspond to the locations where the core model is installed on your machine:

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

If you are running multiple core models in parallel, you'll need to have a large amount of free
disk space, as each instance of the model lives in its own directory that is a complete copy of 
the original model.  You will need about 15 GB of working free disk space for every experiment 
you run.  Once each experiment is complete the working directory for that experiment can be 
safely deleted, recovering 11+ GB of space.