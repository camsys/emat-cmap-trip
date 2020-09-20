""" core_files.py - to create a TMIP-EMAT interface with CMAP EMME Model """
import tempfile
import platform
import emat
import os
import shutil
import pandas as pd
import numpy as np
import re
import subprocess
import warnings

try:
	import shortuuid
except ImportError:
	from uuid import uuid4 as uuid
else:
	shortuuid.set_alphabet(shortuuid.get_alphabet().lower())
	uuid = shortuuid.uuid

from distutils.dir_util import copy_tree

import warnings
from emat import Scope
from emat import SQLiteDB
from emat.database.database import Database
from emat.model.core_files import FilesCoreModel
from emat.model.core_files.parsers import TableParser, MappingParser, loc, key
from emat.util.show_dir import show_dir, show_file_contents
from emat.util.loggers import get_module_logger

_logger = get_module_logger(__name__)

# The demo model code is located in the same
# directory as this script file.  We can recover
# this directory name like this, even if the
# current working directory is different.
# In your application, you may want to program
# this differently, possibly hard-coding the name
# of the model directory.
this_directory = os.path.dirname(__file__)

## Check that EMME is installed and available.  Add to path if needed
import subprocess, os
version_check = subprocess.run(["emme", "--version"], shell=True, capture_output=True)
version_check_emme = re.compile("Emme 4\.3\.[0-9] 64-bit")
if not version_check_emme.match(version_check.stdout.decode()):
	local_env = os.environ
	for patch_num in [9,8,7,6]:
		if os.path.exists(f"C:/Program Files/INRO/Emme/Emme 4/Emme-4.3.{patch_num}"):
			local_env["PATH"] += fr";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.{patch_num}\programs"
			local_env["PATH"] += fr";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.{patch_num}/Python27"
			local_env["PATH"] += fr";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.{patch_num}/Python27\Scripts"
	version_check = subprocess.run(["emme", "--version"], shell=True, capture_output=True)
	if not version_check_emme.match(version_check.stdout.decode()):
		warnings.warn("cannot find Emme 4.3.* 64-bit")
if version_check.stdout:
	_logger.info(version_check.stdout.decode())

# Windows PowerShell
# $env:Path += ";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.7\programs"
# $env:Path += ";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.7\Python27"
# $env:Path += ";C:\Program Files\INRO\Emme\Emme 4\Emme-4.3.7\programs\Scripts"

def to_simple_python(v):
	try:
		isfloat = np.issubdtype(v, np.floating)
	except:
		isfloat = False
	try:
		isint = np.issubdtype(v, np.integer)
	except:
		isint = False
	if isfloat:
		return float(v)
	elif isint:
		return int(v)
	else:
		return v

def template(filename):
	"""The path to a template file."""
	return os.path.join(this_directory, 'templates', filename)

def join_norm(*args):
	"""Normalize a joined path."""
	return os.path.normpath(os.path.join(*args))

class ReplacementOfNumber:
	numbr = r"([-+]?\d*\.?\d*[eE]?[-+]?\d*|\d+\/\d+)"  # matches any number representation
	def __init__(self, varname, assign_operator=":", logger=_logger):
		# This is to create a tool that will search through a text file, finding instances of the general
		# form "varname: 123.456", and be able to replace the value 123.456 with some other number.
		# The assignment_operator is set to the colon character, but it can be replaced with "=" or "<-"
		# or whatever assignment operator is used in the text of the file being modified.
		self.varname = varname
		self.regex = re.compile(f"({varname}\s*{assign_operator}\s*)({self.numbr})")
		self.logger = logger

	def sub(self, value, s):
		s, n = self.regex.subn(f"\g<1>{value}", s)
		if self.logger is not None:
			self.logger.info(f"For '{self.varname}': {n} substitutions made")
		return s

class ReplacementOfString:
	def __init__(self, varname, assign_operator=":", logger=_logger):
		self.varname = varname
		self.regex = re.compile(f"({varname}\s*{assign_operator}\s*)([^#\n]*)(#.*)?", flags=re.MULTILINE)
		self.logger = logger

	def sub(self, value, s):
		# This implementation of the replacement algorithm preserves
		# comments appended after the value using the hash # character.
		s, n = self.regex.subn(f"\g<1>{value}  \g<3>", s)
		if self.logger is not None:
			self.logger.info(f"For '{self.varname}': {n} substitutions made")
		return s

class CMAP_EMAT_Model(FilesCoreModel):

	def __init__(self, db=None, unique_id=None, ephemeral=False, db_filename="cmap-emat-database.sqlitedb"):

		_logger.info("CMAP EMAT Model INIT...")

		self.ephemeral = ephemeral
		self.unique_id = unique_id

		scope = Scope("cmap-trip-scope.yml")

		if db is None:
			if os.path.exists(db_filename):
				initialize = False
				_logger.info(f"CMAP EMAT database file {db_filename} exists")
			else:
				initialize = True
				_logger.info(f"CMAP EMAT database file {db_filename} does not exist, initializing...")
			db = SQLiteDB(
				db_filename,
				initialize=initialize,
			)
		if db is False: # explicitly use no DB
			db = None
			_logger.warn(f"CMAP EMAT database usage disabled")
		else:
			try:
				db.store_scope(scope)
			except KeyError:
				pass

		# Initialize the super class (FilesCoreModel)
		super().__init__(
			configuration="cmap-trip-model-config.yml",
			scope=scope,
			db=db,
			name='CMAP_Trip_Based_Model',
			local_directory=os.getcwd(),
		)
		if isinstance(db, SQLiteDB):
			self._sqlitedb_path = db.database_path

		self.source_model_path = self.resolved_model_path

		source_model_paths = {
			'base': join_norm(self.source_model_path, self.config['model_path_land_use_base']),
			'alt': join_norm(self.source_model_path, self.config['model_path_land_use_alt1']),
		}

		for k,v in source_model_paths.items():
			if not os.path.exists(v):
				warnings.warn(f"{k} core model is not available at {v}")

		# Add parsers to instruct the load_measures function
		# how to parse the outputs and get the measure values.

		self.add_parser(
			MappingParser(
				os.path.join('Database', 'report', "run_vmt_statistics.rpt"),
				{
					# 'Full_Region_VMT': sum(key['Chicago.Expressway VMT'             ]),
					#
					#
					'Chicago_Expressway_VMT':              key['Chicago.Expressway VMT'             ],
					'Chicago_Arterial_VMT':                key['Chicago.Arterial VMT'               ],
					'Chicago_RampToll_VMT':                key['Chicago.Ramp/Toll VMT'              ],
					'Chicago_Centroid_VMT':                key['Chicago.Centroid VMT'               ],
					'Chicago_Total_District_VMT':          key['Chicago.Total District VMT'         ],
					'Cook_balance_Expressway_VMT':         key['Cook balance.Expressway VMT'        ],
					'Cook_balance_Arterial_VMT':           key['Cook balance.Arterial VMT'          ],
					'Cook_balance_RampToll_VMT':           key['Cook balance.Ramp/Toll VMT'         ],
					'Cook_balance_Centroid_VMT':           key['Cook balance.Centroid VMT'          ],
					'Cook_balance_Total_District_VMT':     key['Cook balance.Total District VMT'    ],
					'DuPage_Expressway_VMT':               key['DuPage.Expressway VMT'              ],
					'DuPage_Arterial_VMT':                 key['DuPage.Arterial VMT'                ],
					'DuPage_RampToll_VMT':                 key['DuPage.Ramp/Toll VMT'               ],
					'DuPage_Centroid_VMT':                 key['DuPage.Centroid VMT'                ],
					'DuPage_Total_District_VMT':           key['DuPage.Total District VMT'          ],
					'Kane_Expressway_VMT':                 key['Kane.Expressway VMT'                ],
					'Kane_Arterial_VMT':                   key['Kane.Arterial VMT'                  ],
					'Kane_RampToll_VMT':                   key['Kane.Ramp/Toll VMT'                 ],
					'Kane_Centroid_VMT':                   key['Kane.Centroid VMT'                  ],
					'Kane_Total_District_VMT':             key['Kane.Total District VMT'            ],
					'Kendall_Expressway_VMT':              key['Kendall.Expressway VMT'             ],
					'Kendall_Arterial_VMT':                key['Kendall.Arterial VMT'               ],
					'Kendall_RampToll_VMT':                key['Kendall.Ramp/Toll VMT'              ],
					'Kendall_Centroid_VMT':                key['Kendall.Centroid VMT'               ],
					'Kendall_Total_District_VMT':          key['Kendall.Total District VMT'         ],
					'Lake_Expressway_VMT':                 key['Lake.Expressway VMT'                ],
					'Lake_Arterial_VMT':                   key['Lake.Arterial VMT'                  ],
					'Lake_RampToll_VMT':                   key['Lake.Ramp/Toll VMT'                 ],
					'Lake_Centroid_VMT':                   key['Lake.Centroid VMT'                  ],
					'Lake_Total_District_VMT':             key['Lake.Total District VMT'            ],
					'McHenry_Expressway_VMT':              key['McHenry.Expressway VMT'             ],
					'McHenry_Arterial_VMT':                key['McHenry.Arterial VMT'               ],
					'McHenry_RampToll_VMT':                key['McHenry.Ramp/Toll VMT'              ],
					'McHenry_Centroid_VMT':                key['McHenry.Centroid VMT'               ],
					'McHenry_Total_District_VMT':          key['McHenry.Total District VMT'         ],
					'Will_Expressway_VMT':                 key['Will.Expressway VMT'                ],
					'Will_Arterial_VMT':                   key['Will.Arterial VMT'                  ],
					'Will_RampToll_VMT':                   key['Will.Ramp/Toll VMT'                 ],
					'Will_Centroid_VMT':                   key['Will.Centroid VMT'                  ],
					'Will_Total_District_VMT':             key['Will.Total District VMT'            ],
					'Illinois_balance_Expressway_VMT':     key['Illinois balance.Expressway VMT'    ],
					'Illinois_balance_Arterial_VMT':       key['Illinois balance.Arterial VMT'      ],
					'Illinois_balance_RampToll_VMT':       key['Illinois balance.Ramp/Toll VMT'     ],
					'Illinois_balance_Centroid_VMT':       key['Illinois balance.Centroid VMT'      ],
					'Illinois_balance_Total_District_VMT': key['Illinois balance.Total District VMT'],
					'Indiana_Expressway_VMT':              key['Indiana.Expressway VMT'             ],
					'Indiana_Arterial_VMT':                key['Indiana.Arterial VMT'               ],
					'Indiana_RampToll_VMT':                key['Indiana.Ramp/Toll VMT'              ],
					'Indiana_Centroid_VMT':                key['Indiana.Centroid VMT'               ],
					'Indiana_Total_District_VMT':          key['Indiana.Total District VMT'         ],
					'Wisconsin_Expressway_VMT':            key['Wisconsin.Expressway VMT'           ],
					'Wisconsin_Arterial_VMT':              key['Wisconsin.Arterial VMT'             ],
					'Wisconsin_RampToll_VMT':              key['Wisconsin.Ramp/Toll VMT'            ],
					'Wisconsin_Centroid_VMT':              key['Wisconsin.Centroid VMT'             ],
					'Wisconsin_Total_District_VMT':        key['Wisconsin.Total District VMT'       ],
				},
				reader_method=tiered_file_parse_colon,
			)
		)

		self.add_parser(
			MappingParser(
				os.path.join('Database', 'report', "run_vht_statistics.rpt"),
				{
					'Chicago_Autos_Expressway_VMT': key['Chicago.Autos.Expressway VMT'],
					'Chicago_Autos_Arterial_VMT': key['Chicago.Autos.Arterial VMT'],
					'Chicago_Autos_RampToll_VMT': key['Chicago.Autos.Ramp/Toll VMT'],
					'Chicago_Autos_Centroid_VMT': key['Chicago.Autos.Centroid VMT'],
					'Chicago_Autos_Total_District_VMT': key['Chicago.Autos.Total District VMT'],
					'Chicago_B-plate_Trucks_Expressway_VMT': key['Chicago.B-plate Trucks.Expressway VMT'],
					'Chicago_B-plate_Trucks_Arterial_VMT': key['Chicago.B-plate Trucks.Arterial VMT'],
					'Chicago_B-plate_Trucks_RampToll_VMT': key['Chicago.B-plate Trucks.Ramp/Toll VMT'],
					'Chicago_B-plate_Trucks_Centroid_VMT': key['Chicago.B-plate Trucks.Centroid VMT'],
					'Chicago_B-plate_Trucks_Total_District_VMT': key['Chicago.B-plate Trucks.Total District VMT'],
					'Chicago_Light_Trucks_Expressway_VMT': key['Chicago.Light Trucks.Expressway VMT'],
					'Chicago_Light_Trucks_Arterial_VMT': key['Chicago.Light Trucks.Arterial VMT'],
					'Chicago_Light_Trucks_RampToll_VMT': key['Chicago.Light Trucks.Ramp/Toll VMT'],
					'Chicago_Light_Trucks_Centroid_VMT': key['Chicago.Light Trucks.Centroid VMT'],
					'Chicago_Light_Trucks_Total_District_VMT': key['Chicago.Light Trucks.Total District VMT'],
					'Chicago_Medium_Trucks_Expressway_VMT': key['Chicago.Medium Trucks.Expressway VMT'],
					'Chicago_Medium_Trucks_Arterial_VMT': key['Chicago.Medium Trucks.Arterial VMT'],
					'Chicago_Medium_Trucks_RampToll_VMT': key['Chicago.Medium Trucks.Ramp/Toll VMT'],
					'Chicago_Medium_Trucks_Centroid_VMT': key['Chicago.Medium Trucks.Centroid VMT'],
					'Chicago_Medium_Trucks_Total_District_VMT': key['Chicago.Medium Trucks.Total District VMT'],
					'Chicago_Heavy_Trucks_Expressway_VMT': key['Chicago.Heavy Trucks.Expressway VMT'],
					'Chicago_Heavy_Trucks_Arterial_VMT': key['Chicago.Heavy Trucks.Arterial VMT'],
					'Chicago_Heavy_Trucks_RampToll_VMT': key['Chicago.Heavy Trucks.Ramp/Toll VMT'],
					'Chicago_Heavy_Trucks_Centroid_VMT': key['Chicago.Heavy Trucks.Centroid VMT'],
					'Chicago_Heavy_Trucks_Total_District_VMT': key['Chicago.Heavy Trucks.Total District VMT'],
					'Chicago_Total_VHT_Expressway_VHT': key['Chicago.Total VHT.Expressway VHT'],
					'Chicago_Total_VHT_Arterial_VHT': key['Chicago.Total VHT.Arterial VHT'],
					'Chicago_Total_VHT_RampToll_VHT': key['Chicago.Total VHT.Ramp/Toll VHT'],
					'Chicago_Total_VHT_Centroid_VHT': key['Chicago.Total VHT.Centroid VHT'],
					'Chicago_Total_VHT_Total_District_VHT': key['Chicago.Total VHT.Total District VHT'],
					'Cook_balance_Autos_Expressway_VMT': key['Cook balance.Autos.Expressway VMT'],
					'Cook_balance_Autos_Arterial_VMT': key['Cook balance.Autos.Arterial VMT'],
					'Cook_balance_Autos_RampToll_VMT': key['Cook balance.Autos.Ramp/Toll VMT'],
					'Cook_balance_Autos_Centroid_VMT': key['Cook balance.Autos.Centroid VMT'],
					'Cook_balance_Autos_Total_District_VMT': key['Cook balance.Autos.Total District VMT'],
					'Cook_balance_B-plate_Trucks_Expressway_VMT': key['Cook balance.B-plate Trucks.Expressway VMT'],
					'Cook_balance_B-plate_Trucks_Arterial_VMT': key['Cook balance.B-plate Trucks.Arterial VMT'],
					'Cook_balance_B-plate_Trucks_RampToll_VMT': key['Cook balance.B-plate Trucks.Ramp/Toll VMT'],
					'Cook_balance_B-plate_Trucks_Centroid_VMT': key['Cook balance.B-plate Trucks.Centroid VMT'],
					'Cook_balance_B-plate_Trucks_Total_District_VMT': key[
						'Cook balance.B-plate Trucks.Total District VMT'],
					'Cook_balance_Light_Trucks_Expressway_VMT': key['Cook balance.Light Trucks.Expressway VMT'],
					'Cook_balance_Light_Trucks_Arterial_VMT': key['Cook balance.Light Trucks.Arterial VMT'],
					'Cook_balance_Light_Trucks_RampToll_VMT': key['Cook balance.Light Trucks.Ramp/Toll VMT'],
					'Cook_balance_Light_Trucks_Centroid_VMT': key['Cook balance.Light Trucks.Centroid VMT'],
					'Cook_balance_Light_Trucks_Total_District_VMT': key['Cook balance.Light Trucks.Total District VMT'],
					'Cook_balance_Medium_Trucks_Expressway_VMT': key['Cook balance.Medium Trucks.Expressway VMT'],
					'Cook_balance_Medium_Trucks_Arterial_VMT': key['Cook balance.Medium Trucks.Arterial VMT'],
					'Cook_balance_Medium_Trucks_RampToll_VMT': key['Cook balance.Medium Trucks.Ramp/Toll VMT'],
					'Cook_balance_Medium_Trucks_Centroid_VMT': key['Cook balance.Medium Trucks.Centroid VMT'],
					'Cook_balance_Medium_Trucks_Total_District_VMT': key[
						'Cook balance.Medium Trucks.Total District VMT'],
					'Cook_balance_Heavy_Trucks_Expressway_VMT': key['Cook balance.Heavy Trucks.Expressway VMT'],
					'Cook_balance_Heavy_Trucks_Arterial_VMT': key['Cook balance.Heavy Trucks.Arterial VMT'],
					'Cook_balance_Heavy_Trucks_RampToll_VMT': key['Cook balance.Heavy Trucks.Ramp/Toll VMT'],
					'Cook_balance_Heavy_Trucks_Centroid_VMT': key['Cook balance.Heavy Trucks.Centroid VMT'],
					'Cook_balance_Heavy_Trucks_Total_District_VMT': key['Cook balance.Heavy Trucks.Total District VMT'],
					'Cook_balance_Total_VHT_Expressway_VHT': key['Cook balance.Total VHT.Expressway VHT'],
					'Cook_balance_Total_VHT_Arterial_VHT': key['Cook balance.Total VHT.Arterial VHT'],
					'Cook_balance_Total_VHT_RampToll_VHT': key['Cook balance.Total VHT.Ramp/Toll VHT'],
					'Cook_balance_Total_VHT_Centroid_VHT': key['Cook balance.Total VHT.Centroid VHT'],
					'Cook_balance_Total_VHT_Total_District_VHT': key['Cook balance.Total VHT.Total District VHT'],
					'DuPage_Autos_Expressway_VMT': key['DuPage.Autos.Expressway VMT'],
					'DuPage_Autos_Arterial_VMT': key['DuPage.Autos.Arterial VMT'],
					'DuPage_Autos_RampToll_VMT': key['DuPage.Autos.Ramp/Toll VMT'],
					'DuPage_Autos_Centroid_VMT': key['DuPage.Autos.Centroid VMT'],
					'DuPage_Autos_Total_District_VMT': key['DuPage.Autos.Total District VMT'],
					'DuPage_B-plate_Trucks_Expressway_VMT': key['DuPage.B-plate Trucks.Expressway VMT'],
					'DuPage_B-plate_Trucks_Arterial_VMT': key['DuPage.B-plate Trucks.Arterial VMT'],
					'DuPage_B-plate_Trucks_RampToll_VMT': key['DuPage.B-plate Trucks.Ramp/Toll VMT'],
					'DuPage_B-plate_Trucks_Centroid_VMT': key['DuPage.B-plate Trucks.Centroid VMT'],
					'DuPage_B-plate_Trucks_Total_District_VMT': key['DuPage.B-plate Trucks.Total District VMT'],
					'DuPage_Light_Trucks_Expressway_VMT': key['DuPage.Light Trucks.Expressway VMT'],
					'DuPage_Light_Trucks_Arterial_VMT': key['DuPage.Light Trucks.Arterial VMT'],
					'DuPage_Light_Trucks_RampToll_VMT': key['DuPage.Light Trucks.Ramp/Toll VMT'],
					'DuPage_Light_Trucks_Centroid_VMT': key['DuPage.Light Trucks.Centroid VMT'],
					'DuPage_Light_Trucks_Total_District_VMT': key['DuPage.Light Trucks.Total District VMT'],
					'DuPage_Medium_Trucks_Expressway_VMT': key['DuPage.Medium Trucks.Expressway VMT'],
					'DuPage_Medium_Trucks_Arterial_VMT': key['DuPage.Medium Trucks.Arterial VMT'],
					'DuPage_Medium_Trucks_RampToll_VMT': key['DuPage.Medium Trucks.Ramp/Toll VMT'],
					'DuPage_Medium_Trucks_Centroid_VMT': key['DuPage.Medium Trucks.Centroid VMT'],
					'DuPage_Medium_Trucks_Total_District_VMT': key['DuPage.Medium Trucks.Total District VMT'],
					'DuPage_Heavy_Trucks_Expressway_VMT': key['DuPage.Heavy Trucks.Expressway VMT'],
					'DuPage_Heavy_Trucks_Arterial_VMT': key['DuPage.Heavy Trucks.Arterial VMT'],
					'DuPage_Heavy_Trucks_RampToll_VMT': key['DuPage.Heavy Trucks.Ramp/Toll VMT'],
					'DuPage_Heavy_Trucks_Centroid_VMT': key['DuPage.Heavy Trucks.Centroid VMT'],
					'DuPage_Heavy_Trucks_Total_District_VMT': key['DuPage.Heavy Trucks.Total District VMT'],
					'DuPage_Total_VHT_Expressway_VHT': key['DuPage.Total VHT.Expressway VHT'],
					'DuPage_Total_VHT_Arterial_VHT': key['DuPage.Total VHT.Arterial VHT'],
					'DuPage_Total_VHT_RampToll_VHT': key['DuPage.Total VHT.Ramp/Toll VHT'],
					'DuPage_Total_VHT_Centroid_VHT': key['DuPage.Total VHT.Centroid VHT'],
					'DuPage_Total_VHT_Total_District_VHT': key['DuPage.Total VHT.Total District VHT'],
					'Kane_Autos_Expressway_VMT': key['Kane.Autos.Expressway VMT'],
					'Kane_Autos_Arterial_VMT': key['Kane.Autos.Arterial VMT'],
					'Kane_Autos_RampToll_VMT': key['Kane.Autos.Ramp/Toll VMT'],
					'Kane_Autos_Centroid_VMT': key['Kane.Autos.Centroid VMT'],
					'Kane_Autos_Total_District_VMT': key['Kane.Autos.Total District VMT'],
					'Kane_B-plate_Trucks_Expressway_VMT': key['Kane.B-plate Trucks.Expressway VMT'],
					'Kane_B-plate_Trucks_Arterial_VMT': key['Kane.B-plate Trucks.Arterial VMT'],
					'Kane_B-plate_Trucks_RampToll_VMT': key['Kane.B-plate Trucks.Ramp/Toll VMT'],
					'Kane_B-plate_Trucks_Centroid_VMT': key['Kane.B-plate Trucks.Centroid VMT'],
					'Kane_B-plate_Trucks_Total_District_VMT': key['Kane.B-plate Trucks.Total District VMT'],
					'Kane_Light_Trucks_Expressway_VMT': key['Kane.Light Trucks.Expressway VMT'],
					'Kane_Light_Trucks_Arterial_VMT': key['Kane.Light Trucks.Arterial VMT'],
					'Kane_Light_Trucks_RampToll_VMT': key['Kane.Light Trucks.Ramp/Toll VMT'],
					'Kane_Light_Trucks_Centroid_VMT': key['Kane.Light Trucks.Centroid VMT'],
					'Kane_Light_Trucks_Total_District_VMT': key['Kane.Light Trucks.Total District VMT'],
					'Kane_Medium_Trucks_Expressway_VMT': key['Kane.Medium Trucks.Expressway VMT'],
					'Kane_Medium_Trucks_Arterial_VMT': key['Kane.Medium Trucks.Arterial VMT'],
					'Kane_Medium_Trucks_RampToll_VMT': key['Kane.Medium Trucks.Ramp/Toll VMT'],
					'Kane_Medium_Trucks_Centroid_VMT': key['Kane.Medium Trucks.Centroid VMT'],
					'Kane_Medium_Trucks_Total_District_VMT': key['Kane.Medium Trucks.Total District VMT'],
					'Kane_Heavy_Trucks_Expressway_VMT': key['Kane.Heavy Trucks.Expressway VMT'],
					'Kane_Heavy_Trucks_Arterial_VMT': key['Kane.Heavy Trucks.Arterial VMT'],
					'Kane_Heavy_Trucks_RampToll_VMT': key['Kane.Heavy Trucks.Ramp/Toll VMT'],
					'Kane_Heavy_Trucks_Centroid_VMT': key['Kane.Heavy Trucks.Centroid VMT'],
					'Kane_Heavy_Trucks_Total_District_VMT': key['Kane.Heavy Trucks.Total District VMT'],
					'Kane_Total_VHT_Expressway_VHT': key['Kane.Total VHT.Expressway VHT'],
					'Kane_Total_VHT_Arterial_VHT': key['Kane.Total VHT.Arterial VHT'],
					'Kane_Total_VHT_RampToll_VHT': key['Kane.Total VHT.Ramp/Toll VHT'],
					'Kane_Total_VHT_Centroid_VHT': key['Kane.Total VHT.Centroid VHT'],
					'Kane_Total_VHT_Total_District_VHT': key['Kane.Total VHT.Total District VHT'],
					'Kendall_Autos_Expressway_VMT': key['Kendall.Autos.Expressway VMT'],
					'Kendall_Autos_Arterial_VMT': key['Kendall.Autos.Arterial VMT'],
					'Kendall_Autos_RampToll_VMT': key['Kendall.Autos.Ramp/Toll VMT'],
					'Kendall_Autos_Centroid_VMT': key['Kendall.Autos.Centroid VMT'],
					'Kendall_Autos_Total_District_VMT': key['Kendall.Autos.Total District VMT'],
					'Kendall_B-plate_Trucks_Expressway_VMT': key['Kendall.B-plate Trucks.Expressway VMT'],
					'Kendall_B-plate_Trucks_Arterial_VMT': key['Kendall.B-plate Trucks.Arterial VMT'],
					'Kendall_B-plate_Trucks_RampToll_VMT': key['Kendall.B-plate Trucks.Ramp/Toll VMT'],
					'Kendall_B-plate_Trucks_Centroid_VMT': key['Kendall.B-plate Trucks.Centroid VMT'],
					'Kendall_B-plate_Trucks_Total_District_VMT': key['Kendall.B-plate Trucks.Total District VMT'],
					'Kendall_Light_Trucks_Expressway_VMT': key['Kendall.Light Trucks.Expressway VMT'],
					'Kendall_Light_Trucks_Arterial_VMT': key['Kendall.Light Trucks.Arterial VMT'],
					'Kendall_Light_Trucks_RampToll_VMT': key['Kendall.Light Trucks.Ramp/Toll VMT'],
					'Kendall_Light_Trucks_Centroid_VMT': key['Kendall.Light Trucks.Centroid VMT'],
					'Kendall_Light_Trucks_Total_District_VMT': key['Kendall.Light Trucks.Total District VMT'],
					'Kendall_Medium_Trucks_Expressway_VMT': key['Kendall.Medium Trucks.Expressway VMT'],
					'Kendall_Medium_Trucks_Arterial_VMT': key['Kendall.Medium Trucks.Arterial VMT'],
					'Kendall_Medium_Trucks_RampToll_VMT': key['Kendall.Medium Trucks.Ramp/Toll VMT'],
					'Kendall_Medium_Trucks_Centroid_VMT': key['Kendall.Medium Trucks.Centroid VMT'],
					'Kendall_Medium_Trucks_Total_District_VMT': key['Kendall.Medium Trucks.Total District VMT'],
					'Kendall_Heavy_Trucks_Expressway_VMT': key['Kendall.Heavy Trucks.Expressway VMT'],
					'Kendall_Heavy_Trucks_Arterial_VMT': key['Kendall.Heavy Trucks.Arterial VMT'],
					'Kendall_Heavy_Trucks_RampToll_VMT': key['Kendall.Heavy Trucks.Ramp/Toll VMT'],
					'Kendall_Heavy_Trucks_Centroid_VMT': key['Kendall.Heavy Trucks.Centroid VMT'],
					'Kendall_Heavy_Trucks_Total_District_VMT': key['Kendall.Heavy Trucks.Total District VMT'],
					'Kendall_Total_VHT_Expressway_VHT': key['Kendall.Total VHT.Expressway VHT'],
					'Kendall_Total_VHT_Arterial_VHT': key['Kendall.Total VHT.Arterial VHT'],
					'Kendall_Total_VHT_RampToll_VHT': key['Kendall.Total VHT.Ramp/Toll VHT'],
					'Kendall_Total_VHT_Centroid_VHT': key['Kendall.Total VHT.Centroid VHT'],
					'Kendall_Total_VHT_Total_District_VHT': key['Kendall.Total VHT.Total District VHT'],
					'Lake_Autos_Expressway_VMT': key['Lake.Autos.Expressway VMT'],
					'Lake_Autos_Arterial_VMT': key['Lake.Autos.Arterial VMT'],
					'Lake_Autos_RampToll_VMT': key['Lake.Autos.Ramp/Toll VMT'],
					'Lake_Autos_Centroid_VMT': key['Lake.Autos.Centroid VMT'],
					'Lake_Autos_Total_District_VMT': key['Lake.Autos.Total District VMT'],
					'Lake_B-plate_Trucks_Expressway_VMT': key['Lake.B-plate Trucks.Expressway VMT'],
					'Lake_B-plate_Trucks_Arterial_VMT': key['Lake.B-plate Trucks.Arterial VMT'],
					'Lake_B-plate_Trucks_RampToll_VMT': key['Lake.B-plate Trucks.Ramp/Toll VMT'],
					'Lake_B-plate_Trucks_Centroid_VMT': key['Lake.B-plate Trucks.Centroid VMT'],
					'Lake_B-plate_Trucks_Total_District_VMT': key['Lake.B-plate Trucks.Total District VMT'],
					'Lake_Light_Trucks_Expressway_VMT': key['Lake.Light Trucks.Expressway VMT'],
					'Lake_Light_Trucks_Arterial_VMT': key['Lake.Light Trucks.Arterial VMT'],
					'Lake_Light_Trucks_RampToll_VMT': key['Lake.Light Trucks.Ramp/Toll VMT'],
					'Lake_Light_Trucks_Centroid_VMT': key['Lake.Light Trucks.Centroid VMT'],
					'Lake_Light_Trucks_Total_District_VMT': key['Lake.Light Trucks.Total District VMT'],
					'Lake_Medium_Trucks_Expressway_VMT': key['Lake.Medium Trucks.Expressway VMT'],
					'Lake_Medium_Trucks_Arterial_VMT': key['Lake.Medium Trucks.Arterial VMT'],
					'Lake_Medium_Trucks_RampToll_VMT': key['Lake.Medium Trucks.Ramp/Toll VMT'],
					'Lake_Medium_Trucks_Centroid_VMT': key['Lake.Medium Trucks.Centroid VMT'],
					'Lake_Medium_Trucks_Total_District_VMT': key['Lake.Medium Trucks.Total District VMT'],
					'Lake_Heavy_Trucks_Expressway_VMT': key['Lake.Heavy Trucks.Expressway VMT'],
					'Lake_Heavy_Trucks_Arterial_VMT': key['Lake.Heavy Trucks.Arterial VMT'],
					'Lake_Heavy_Trucks_RampToll_VMT': key['Lake.Heavy Trucks.Ramp/Toll VMT'],
					'Lake_Heavy_Trucks_Centroid_VMT': key['Lake.Heavy Trucks.Centroid VMT'],
					'Lake_Heavy_Trucks_Total_District_VMT': key['Lake.Heavy Trucks.Total District VMT'],
					'Lake_Total_VHT_Expressway_VHT': key['Lake.Total VHT.Expressway VHT'],
					'Lake_Total_VHT_Arterial_VHT': key['Lake.Total VHT.Arterial VHT'],
					'Lake_Total_VHT_RampToll_VHT': key['Lake.Total VHT.Ramp/Toll VHT'],
					'Lake_Total_VHT_Centroid_VHT': key['Lake.Total VHT.Centroid VHT'],
					'Lake_Total_VHT_Total_District_VHT': key['Lake.Total VHT.Total District VHT'],
					'McHenry_Autos_Expressway_VMT': key['McHenry.Autos.Expressway VMT'],
					'McHenry_Autos_Arterial_VMT': key['McHenry.Autos.Arterial VMT'],
					'McHenry_Autos_RampToll_VMT': key['McHenry.Autos.Ramp/Toll VMT'],
					'McHenry_Autos_Centroid_VMT': key['McHenry.Autos.Centroid VMT'],
					'McHenry_Autos_Total_District_VMT': key['McHenry.Autos.Total District VMT'],
					'McHenry_B-plate_Trucks_Expressway_VMT': key['McHenry.B-plate Trucks.Expressway VMT'],
					'McHenry_B-plate_Trucks_Arterial_VMT': key['McHenry.B-plate Trucks.Arterial VMT'],
					'McHenry_B-plate_Trucks_RampToll_VMT': key['McHenry.B-plate Trucks.Ramp/Toll VMT'],
					'McHenry_B-plate_Trucks_Centroid_VMT': key['McHenry.B-plate Trucks.Centroid VMT'],
					'McHenry_B-plate_Trucks_Total_District_VMT': key['McHenry.B-plate Trucks.Total District VMT'],
					'McHenry_Light_Trucks_Expressway_VMT': key['McHenry.Light Trucks.Expressway VMT'],
					'McHenry_Light_Trucks_Arterial_VMT': key['McHenry.Light Trucks.Arterial VMT'],
					'McHenry_Light_Trucks_RampToll_VMT': key['McHenry.Light Trucks.Ramp/Toll VMT'],
					'McHenry_Light_Trucks_Centroid_VMT': key['McHenry.Light Trucks.Centroid VMT'],
					'McHenry_Light_Trucks_Total_District_VMT': key['McHenry.Light Trucks.Total District VMT'],
					'McHenry_Medium_Trucks_Expressway_VMT': key['McHenry.Medium Trucks.Expressway VMT'],
					'McHenry_Medium_Trucks_Arterial_VMT': key['McHenry.Medium Trucks.Arterial VMT'],
					'McHenry_Medium_Trucks_RampToll_VMT': key['McHenry.Medium Trucks.Ramp/Toll VMT'],
					'McHenry_Medium_Trucks_Centroid_VMT': key['McHenry.Medium Trucks.Centroid VMT'],
					'McHenry_Medium_Trucks_Total_District_VMT': key['McHenry.Medium Trucks.Total District VMT'],
					'McHenry_Heavy_Trucks_Expressway_VMT': key['McHenry.Heavy Trucks.Expressway VMT'],
					'McHenry_Heavy_Trucks_Arterial_VMT': key['McHenry.Heavy Trucks.Arterial VMT'],
					'McHenry_Heavy_Trucks_RampToll_VMT': key['McHenry.Heavy Trucks.Ramp/Toll VMT'],
					'McHenry_Heavy_Trucks_Centroid_VMT': key['McHenry.Heavy Trucks.Centroid VMT'],
					'McHenry_Heavy_Trucks_Total_District_VMT': key['McHenry.Heavy Trucks.Total District VMT'],
					'McHenry_Total_VHT_Expressway_VHT': key['McHenry.Total VHT.Expressway VHT'],
					'McHenry_Total_VHT_Arterial_VHT': key['McHenry.Total VHT.Arterial VHT'],
					'McHenry_Total_VHT_RampToll_VHT': key['McHenry.Total VHT.Ramp/Toll VHT'],
					'McHenry_Total_VHT_Centroid_VHT': key['McHenry.Total VHT.Centroid VHT'],
					'McHenry_Total_VHT_Total_District_VHT': key['McHenry.Total VHT.Total District VHT'],
					'Will_Autos_Expressway_VMT': key['Will.Autos.Expressway VMT'],
					'Will_Autos_Arterial_VMT': key['Will.Autos.Arterial VMT'],
					'Will_Autos_RampToll_VMT': key['Will.Autos.Ramp/Toll VMT'],
					'Will_Autos_Centroid_VMT': key['Will.Autos.Centroid VMT'],
					'Will_Autos_Total_District_VMT': key['Will.Autos.Total District VMT'],
					'Will_B-plate_Trucks_Expressway_VMT': key['Will.B-plate Trucks.Expressway VMT'],
					'Will_B-plate_Trucks_Arterial_VMT': key['Will.B-plate Trucks.Arterial VMT'],
					'Will_B-plate_Trucks_RampToll_VMT': key['Will.B-plate Trucks.Ramp/Toll VMT'],
					'Will_B-plate_Trucks_Centroid_VMT': key['Will.B-plate Trucks.Centroid VMT'],
					'Will_B-plate_Trucks_Total_District_VMT': key['Will.B-plate Trucks.Total District VMT'],
					'Will_Light_Trucks_Expressway_VMT': key['Will.Light Trucks.Expressway VMT'],
					'Will_Light_Trucks_Arterial_VMT': key['Will.Light Trucks.Arterial VMT'],
					'Will_Light_Trucks_RampToll_VMT': key['Will.Light Trucks.Ramp/Toll VMT'],
					'Will_Light_Trucks_Centroid_VMT': key['Will.Light Trucks.Centroid VMT'],
					'Will_Light_Trucks_Total_District_VMT': key['Will.Light Trucks.Total District VMT'],
					'Will_Medium_Trucks_Expressway_VMT': key['Will.Medium Trucks.Expressway VMT'],
					'Will_Medium_Trucks_Arterial_VMT': key['Will.Medium Trucks.Arterial VMT'],
					'Will_Medium_Trucks_RampToll_VMT': key['Will.Medium Trucks.Ramp/Toll VMT'],
					'Will_Medium_Trucks_Centroid_VMT': key['Will.Medium Trucks.Centroid VMT'],
					'Will_Medium_Trucks_Total_District_VMT': key['Will.Medium Trucks.Total District VMT'],
					'Will_Heavy_Trucks_Expressway_VMT': key['Will.Heavy Trucks.Expressway VMT'],
					'Will_Heavy_Trucks_Arterial_VMT': key['Will.Heavy Trucks.Arterial VMT'],
					'Will_Heavy_Trucks_RampToll_VMT': key['Will.Heavy Trucks.Ramp/Toll VMT'],
					'Will_Heavy_Trucks_Centroid_VMT': key['Will.Heavy Trucks.Centroid VMT'],
					'Will_Heavy_Trucks_Total_District_VMT': key['Will.Heavy Trucks.Total District VMT'],
					'Will_Total_VHT_Expressway_VHT': key['Will.Total VHT.Expressway VHT'],
					'Will_Total_VHT_Arterial_VHT': key['Will.Total VHT.Arterial VHT'],
					'Will_Total_VHT_RampToll_VHT': key['Will.Total VHT.Ramp/Toll VHT'],
					'Will_Total_VHT_Centroid_VHT': key['Will.Total VHT.Centroid VHT'],
					'Will_Total_VHT_Total_District_VHT': key['Will.Total VHT.Total District VHT'],
					'Illinois_balance_Autos_Expressway_VMT': key['Illinois balance.Autos.Expressway VMT'],
					'Illinois_balance_Autos_Arterial_VMT': key['Illinois balance.Autos.Arterial VMT'],
					'Illinois_balance_Autos_RampToll_VMT': key['Illinois balance.Autos.Ramp/Toll VMT'],
					'Illinois_balance_Autos_Centroid_VMT': key['Illinois balance.Autos.Centroid VMT'],
					'Illinois_balance_Autos_Total_District_VMT': key['Illinois balance.Autos.Total District VMT'],
					'Illinois_balance_B-plate_Trucks_Expressway_VMT': key[
						'Illinois balance.B-plate Trucks.Expressway VMT'],
					'Illinois_balance_B-plate_Trucks_Arterial_VMT': key['Illinois balance.B-plate Trucks.Arterial VMT'],
					'Illinois_balance_B-plate_Trucks_RampToll_VMT': key[
						'Illinois balance.B-plate Trucks.Ramp/Toll VMT'],
					'Illinois_balance_B-plate_Trucks_Centroid_VMT': key['Illinois balance.B-plate Trucks.Centroid VMT'],
					'Illinois_balance_B-plate_Trucks_Total_District_VMT': key[
						'Illinois balance.B-plate Trucks.Total District VMT'],
					'Illinois_balance_Light_Trucks_Expressway_VMT': key['Illinois balance.Light Trucks.Expressway VMT'],
					'Illinois_balance_Light_Trucks_Arterial_VMT': key['Illinois balance.Light Trucks.Arterial VMT'],
					'Illinois_balance_Light_Trucks_RampToll_VMT': key['Illinois balance.Light Trucks.Ramp/Toll VMT'],
					'Illinois_balance_Light_Trucks_Centroid_VMT': key['Illinois balance.Light Trucks.Centroid VMT'],
					'Illinois_balance_Light_Trucks_Total_District_VMT': key[
						'Illinois balance.Light Trucks.Total District VMT'],
					'Illinois_balance_Medium_Trucks_Expressway_VMT': key[
						'Illinois balance.Medium Trucks.Expressway VMT'],
					'Illinois_balance_Medium_Trucks_Arterial_VMT': key['Illinois balance.Medium Trucks.Arterial VMT'],
					'Illinois_balance_Medium_Trucks_RampToll_VMT': key['Illinois balance.Medium Trucks.Ramp/Toll VMT'],
					'Illinois_balance_Medium_Trucks_Centroid_VMT': key['Illinois balance.Medium Trucks.Centroid VMT'],
					'Illinois_balance_Medium_Trucks_Total_District_VMT': key[
						'Illinois balance.Medium Trucks.Total District VMT'],
					'Illinois_balance_Heavy_Trucks_Expressway_VMT': key['Illinois balance.Heavy Trucks.Expressway VMT'],
					'Illinois_balance_Heavy_Trucks_Arterial_VMT': key['Illinois balance.Heavy Trucks.Arterial VMT'],
					'Illinois_balance_Heavy_Trucks_RampToll_VMT': key['Illinois balance.Heavy Trucks.Ramp/Toll VMT'],
					'Illinois_balance_Heavy_Trucks_Centroid_VMT': key['Illinois balance.Heavy Trucks.Centroid VMT'],
					'Illinois_balance_Heavy_Trucks_Total_District_VMT': key[
						'Illinois balance.Heavy Trucks.Total District VMT'],
					'Illinois_balance_Total_VHT_Expressway_VHT': key['Illinois balance.Total VHT.Expressway VHT'],
					'Illinois_balance_Total_VHT_Arterial_VHT': key['Illinois balance.Total VHT.Arterial VHT'],
					'Illinois_balance_Total_VHT_RampToll_VHT': key['Illinois balance.Total VHT.Ramp/Toll VHT'],
					'Illinois_balance_Total_VHT_Centroid_VHT': key['Illinois balance.Total VHT.Centroid VHT'],
					'Illinois_balance_Total_VHT_Total_District_VHT': key[
						'Illinois balance.Total VHT.Total District VHT'],
					'Indiana_Autos_Expressway_VMT': key['Indiana.Autos.Expressway VMT'],
					'Indiana_Autos_Arterial_VMT': key['Indiana.Autos.Arterial VMT'],
					'Indiana_Autos_RampToll_VMT': key['Indiana.Autos.Ramp/Toll VMT'],
					'Indiana_Autos_Centroid_VMT': key['Indiana.Autos.Centroid VMT'],
					'Indiana_Autos_Total_District_VMT': key['Indiana.Autos.Total District VMT'],
					'Indiana_B-plate_Trucks_Expressway_VMT': key['Indiana.B-plate Trucks.Expressway VMT'],
					'Indiana_B-plate_Trucks_Arterial_VMT': key['Indiana.B-plate Trucks.Arterial VMT'],
					'Indiana_B-plate_Trucks_RampToll_VMT': key['Indiana.B-plate Trucks.Ramp/Toll VMT'],
					'Indiana_B-plate_Trucks_Centroid_VMT': key['Indiana.B-plate Trucks.Centroid VMT'],
					'Indiana_B-plate_Trucks_Total_District_VMT': key['Indiana.B-plate Trucks.Total District VMT'],
					'Indiana_Light_Trucks_Expressway_VMT': key['Indiana.Light Trucks.Expressway VMT'],
					'Indiana_Light_Trucks_Arterial_VMT': key['Indiana.Light Trucks.Arterial VMT'],
					'Indiana_Light_Trucks_RampToll_VMT': key['Indiana.Light Trucks.Ramp/Toll VMT'],
					'Indiana_Light_Trucks_Centroid_VMT': key['Indiana.Light Trucks.Centroid VMT'],
					'Indiana_Light_Trucks_Total_District_VMT': key['Indiana.Light Trucks.Total District VMT'],
					'Indiana_Medium_Trucks_Expressway_VMT': key['Indiana.Medium Trucks.Expressway VMT'],
					'Indiana_Medium_Trucks_Arterial_VMT': key['Indiana.Medium Trucks.Arterial VMT'],
					'Indiana_Medium_Trucks_RampToll_VMT': key['Indiana.Medium Trucks.Ramp/Toll VMT'],
					'Indiana_Medium_Trucks_Centroid_VMT': key['Indiana.Medium Trucks.Centroid VMT'],
					'Indiana_Medium_Trucks_Total_District_VMT': key['Indiana.Medium Trucks.Total District VMT'],
					'Indiana_Heavy_Trucks_Expressway_VMT': key['Indiana.Heavy Trucks.Expressway VMT'],
					'Indiana_Heavy_Trucks_Arterial_VMT': key['Indiana.Heavy Trucks.Arterial VMT'],
					'Indiana_Heavy_Trucks_RampToll_VMT': key['Indiana.Heavy Trucks.Ramp/Toll VMT'],
					'Indiana_Heavy_Trucks_Centroid_VMT': key['Indiana.Heavy Trucks.Centroid VMT'],
					'Indiana_Heavy_Trucks_Total_District_VMT': key['Indiana.Heavy Trucks.Total District VMT'],
					'Indiana_Total_VHT_Expressway_VHT': key['Indiana.Total VHT.Expressway VHT'],
					'Indiana_Total_VHT_Arterial_VHT': key['Indiana.Total VHT.Arterial VHT'],
					'Indiana_Total_VHT_RampToll_VHT': key['Indiana.Total VHT.Ramp/Toll VHT'],
					'Indiana_Total_VHT_Centroid_VHT': key['Indiana.Total VHT.Centroid VHT'],
					'Indiana_Total_VHT_Total_District_VHT': key['Indiana.Total VHT.Total District VHT'],
					'Wisconsin_Autos_Expressway_VMT': key['Wisconsin.Autos.Expressway VMT'],
					'Wisconsin_Autos_Arterial_VMT': key['Wisconsin.Autos.Arterial VMT'],
					'Wisconsin_Autos_RampToll_VMT': key['Wisconsin.Autos.Ramp/Toll VMT'],
					'Wisconsin_Autos_Centroid_VMT': key['Wisconsin.Autos.Centroid VMT'],
					'Wisconsin_Autos_Total_District_VMT': key['Wisconsin.Autos.Total District VMT'],
					'Wisconsin_B-plate_Trucks_Expressway_VMT': key['Wisconsin.B-plate Trucks.Expressway VMT'],
					'Wisconsin_B-plate_Trucks_Arterial_VMT': key['Wisconsin.B-plate Trucks.Arterial VMT'],
					'Wisconsin_B-plate_Trucks_RampToll_VMT': key['Wisconsin.B-plate Trucks.Ramp/Toll VMT'],
					'Wisconsin_B-plate_Trucks_Centroid_VMT': key['Wisconsin.B-plate Trucks.Centroid VMT'],
					'Wisconsin_B-plate_Trucks_Total_District_VMT': key['Wisconsin.B-plate Trucks.Total District VMT'],
					'Wisconsin_Light_Trucks_Expressway_VMT': key['Wisconsin.Light Trucks.Expressway VMT'],
					'Wisconsin_Light_Trucks_Arterial_VMT': key['Wisconsin.Light Trucks.Arterial VMT'],
					'Wisconsin_Light_Trucks_RampToll_VMT': key['Wisconsin.Light Trucks.Ramp/Toll VMT'],
					'Wisconsin_Light_Trucks_Centroid_VMT': key['Wisconsin.Light Trucks.Centroid VMT'],
					'Wisconsin_Light_Trucks_Total_District_VMT': key['Wisconsin.Light Trucks.Total District VMT'],
					'Wisconsin_Medium_Trucks_Expressway_VMT': key['Wisconsin.Medium Trucks.Expressway VMT'],
					'Wisconsin_Medium_Trucks_Arterial_VMT': key['Wisconsin.Medium Trucks.Arterial VMT'],
					'Wisconsin_Medium_Trucks_RampToll_VMT': key['Wisconsin.Medium Trucks.Ramp/Toll VMT'],
					'Wisconsin_Medium_Trucks_Centroid_VMT': key['Wisconsin.Medium Trucks.Centroid VMT'],
					'Wisconsin_Medium_Trucks_Total_District_VMT': key['Wisconsin.Medium Trucks.Total District VMT'],
					'Wisconsin_Heavy_Trucks_Expressway_VMT': key['Wisconsin.Heavy Trucks.Expressway VMT'],
					'Wisconsin_Heavy_Trucks_Arterial_VMT': key['Wisconsin.Heavy Trucks.Arterial VMT'],
					'Wisconsin_Heavy_Trucks_RampToll_VMT': key['Wisconsin.Heavy Trucks.Ramp/Toll VMT'],
					'Wisconsin_Heavy_Trucks_Centroid_VMT': key['Wisconsin.Heavy Trucks.Centroid VMT'],
					'Wisconsin_Heavy_Trucks_Total_District_VMT': key['Wisconsin.Heavy Trucks.Total District VMT'],
					'Wisconsin_Total_VHT_Expressway_VHT': key['Wisconsin.Total VHT.Expressway VHT'],
					'Wisconsin_Total_VHT_Arterial_VHT': key['Wisconsin.Total VHT.Arterial VHT'],
					'Wisconsin_Total_VHT_RampToll_VHT': key['Wisconsin.Total VHT.Ramp/Toll VHT'],
					'Wisconsin_Total_VHT_Centroid_VHT': key['Wisconsin.Total VHT.Centroid VHT'],
					'Wisconsin_Total_VHT_Total_District_VHT': key['Wisconsin.Total VHT.Total District VHT'],
				},
				reader_method=double_tap_tiered_file_parse,
			)
		)

		self.add_parser(
			MappingParser(
				os.path.join('Database', 'report', "final_run_statistics.rpt"),
				{
					'ENTIRE_NETWORK_Person_Trips_HW_Total_Person_Trips':          key['ENTIRE NETWORK.Person Trips.HW Total Person Trips'         ],
					'ENTIRE_NETWORK_Person_Trips_HW_Auto_Person_Trips':           key['ENTIRE NETWORK.Person Trips.HW Auto Person Trips'          ],
					'ENTIRE_NETWORK_Person_Trips_HW_Transit_Person_Trips':        key['ENTIRE NETWORK.Person Trips.HW Transit Person Trips'       ],
					'ENTIRE_NETWORK_Person_Trips_HO_Total_Person_Trips':          key['ENTIRE NETWORK.Person Trips.HO Total Person Trips'         ],
					'ENTIRE_NETWORK_Person_Trips_HO_Auto_Person_Trips':           key['ENTIRE NETWORK.Person Trips.HO Auto Person Trips'          ],
					'ENTIRE_NETWORK_Person_Trips_HO_Transit_Person_Trips':        key['ENTIRE NETWORK.Person Trips.HO Transit Person Trips'       ],
					'ENTIRE_NETWORK_Person_Trips_NH_Total_Person_Trips':          key['ENTIRE NETWORK.Person Trips.NH Total Person Trips'         ],
					'ENTIRE_NETWORK_Person_Trips_NH_Auto_Person_Trips':           key['ENTIRE NETWORK.Person Trips.NH Auto Person Trips'          ],
					'ENTIRE_NETWORK_Person_Trips_NH_Transit_Person_Trips':        key['ENTIRE NETWORK.Person Trips.NH Transit Person Trips'       ],
					'ENTIRE_NETWORK_Transit_Share_HW_Transit_Share':              key['ENTIRE NETWORK.Transit Share.HW Transit Share'             ],
					'ENTIRE_NETWORK_Transit_Share_HO_Transit_Share':              key['ENTIRE NETWORK.Transit Share.HO Transit Share'             ],
					'ENTIRE_NETWORK_Transit_Share_NH_Transit_Share':              key['ENTIRE NETWORK.Transit Share.NH Transit Share'             ],
					'ENTIRE_NETWORK_Transit_Share_Overall_Transit_Share':         key['ENTIRE NETWORK.Transit Share.Overall Transit Share'        ],
					'ENTIRE_NETWORK_Trip_Distance_HW_Trip_Average_Miles':         key['ENTIRE NETWORK.Trip Distance.HW Trip Average Miles'        ],
					'ENTIRE_NETWORK_Trip_Distance_HO_Trip_Average_Miles':         key['ENTIRE NETWORK.Trip Distance.HO Trip Average Miles'        ],
					'ENTIRE_NETWORK_Trip_Distance_NH_Trip_Average_Miles':         key['ENTIRE NETWORK.Trip Distance.NH Trip Average Miles'        ],
					'ENTIRE_NETWORK_Trip_Duration_HW_Trip_Average_Minutes':       key['ENTIRE NETWORK.Trip Duration.HW Trip Average Minutes'      ],
					'ENTIRE_NETWORK_Trip_Duration_HO_Trip_Average_Minutes':       key['ENTIRE NETWORK.Trip Duration.HO Trip Average Minutes'      ],
					'ENTIRE_NETWORK_Trip_Duration_NH_Trip_Average_Minutes':       key['ENTIRE NETWORK.Trip Duration.NH Trip Average Minutes'      ],
					'ENTIRE_NETWORK_Other_Trips_B_Plate_Truck_Trips':             key['ENTIRE NETWORK.Other Trips.B-Plate Truck Trips'            ],
					'ENTIRE_NETWORK_Other_Trips_Light_Truck_Trips':               key['ENTIRE NETWORK.Other Trips.Light Truck Trips'              ],
					'ENTIRE_NETWORK_Other_Trips_Medium_Truck_Trips':              key['ENTIRE NETWORK.Other Trips.Medium Truck Trips'             ],
					'ENTIRE_NETWORK_Other_Trips_Heavy_Truck_Trips':               key['ENTIRE NETWORK.Other Trips.Heavy Truck Trips'              ],
					'ENTIRE_NETWORK_Other_Trips_POE_Auto_Trips':                  key['ENTIRE NETWORK.Other Trips.POE Auto Trips'                 ],
					'ENTIRE_NETWORK_Other_Trips_POE_Truck_Trips':                 key['ENTIRE NETWORK.Other Trips.POE Truck Trips'                ],
					'ENTIRE_NETWORK_Other_Trips_POE_Airport_Trips':               key['ENTIRE NETWORK.Other Trips.POE Airport Trips'              ],
					'NON_ATTAINMENT_AREA_Person_Trips_HW_Total_Person_Trips':     key['NON-ATTAINMENT AREA.Person Trips.HW Total Person Trips'    ],
					'NON_ATTAINMENT_AREA_Person_Trips_HW_Auto_Person_Trips':      key['NON-ATTAINMENT AREA.Person Trips.HW Auto Person Trips'     ],
					'NON_ATTAINMENT_AREA_Person_Trips_HW_Transit_Person_Trips':   key['NON-ATTAINMENT AREA.Person Trips.HW Transit Person Trips'  ],
					'NON_ATTAINMENT_AREA_Person_Trips_HO_Total_Person_Trips':     key['NON-ATTAINMENT AREA.Person Trips.HO Total Person Trips'    ],
					'NON_ATTAINMENT_AREA_Person_Trips_HO_Auto_Person_Trips':      key['NON-ATTAINMENT AREA.Person Trips.HO Auto Person Trips'     ],
					'NON_ATTAINMENT_AREA_Person_Trips_HO_Transit_Person_Trips':   key['NON-ATTAINMENT AREA.Person Trips.HO Transit Person Trips'  ],
					'NON_ATTAINMENT_AREA_Person_Trips_NH_Total_Person_Trips':     key['NON-ATTAINMENT AREA.Person Trips.NH Total Person Trips'    ],
					'NON_ATTAINMENT_AREA_Person_Trips_NH_Auto_Person_Trips':      key['NON-ATTAINMENT AREA.Person Trips.NH Auto Person Trips'     ],
					'NON_ATTAINMENT_AREA_Person_Trips_NH_Transit_Person_Trips':   key['NON-ATTAINMENT AREA.Person Trips.NH Transit Person Trips'  ],
					'NON_ATTAINMENT_AREA_Transit_Share_HW_Transit_Share':         key['NON-ATTAINMENT AREA.Transit Share.HW Transit Share'        ],
					'NON_ATTAINMENT_AREA_Transit_Share_HO_Transit_Share':         key['NON-ATTAINMENT AREA.Transit Share.HO Transit Share'        ],
					'NON_ATTAINMENT_AREA_Transit_Share_NH_Transit_Share':         key['NON-ATTAINMENT AREA.Transit Share.NH Transit Share'        ],
					'NON_ATTAINMENT_AREA_Transit_Share_Overall_Transit_Share':    key['NON-ATTAINMENT AREA.Transit Share.Overall Transit Share'   ],
					'NON_ATTAINMENT_AREA_Trip_Distance_HW_Trip_Average_Miles':    key['NON-ATTAINMENT AREA.Trip Distance.HW Trip Average Miles'   ],
					'NON_ATTAINMENT_AREA_Trip_Distance_HO_Trip_Average_Miles':    key['NON-ATTAINMENT AREA.Trip Distance.HO Trip Average Miles'   ],
					'NON_ATTAINMENT_AREA_Trip_Distance_NH_Trip_Average_Miles':    key['NON-ATTAINMENT AREA.Trip Distance.NH Trip Average Miles'   ],
					'NON_ATTAINMENT_AREA_Trip_Duration_HW_Trip_Average_Minutes':  key['NON-ATTAINMENT AREA.Trip Duration.HW Trip Average Minutes' ],
					'NON_ATTAINMENT_AREA_Trip_Duration_HO_Trip_Average_Minutes':  key['NON-ATTAINMENT AREA.Trip Duration.HO Trip Average Minutes' ],
					'NON_ATTAINMENT_AREA_Trip_Duration_NH_Trip_Average_Minutes':  key['NON-ATTAINMENT AREA.Trip Duration.NH Trip Average Minutes' ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_Auto_VMT':             key['NON-ATTAINMENT AREA.Vehicle Class VMT.Auto VMT'            ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_B_Plate_Truck_VMT':    key['NON-ATTAINMENT AREA.Vehicle Class VMT.B-Plate Truck VMT'   ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_Light_Truck_VMT':      key['NON-ATTAINMENT AREA.Vehicle Class VMT.Light Truck VMT'     ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_Medium_Truck_VMT':     key['NON-ATTAINMENT AREA.Vehicle Class VMT.Medium Truck VMT'    ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_Heavy_Truck_VMT':      key['NON-ATTAINMENT AREA.Vehicle Class VMT.Heavy Truck VMT'     ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_Bus_VMT':              key['NON-ATTAINMENT AREA.Vehicle Class VMT.Bus VMT'             ],
					'NON_ATTAINMENT_AREA_Vehicle_Class_VMT_All_VMT':              key['NON-ATTAINMENT AREA.Vehicle Class VMT.All VMT'             ],
				},
				reader_method=tiered_file_parse_colon,
			)
		)

		self.add_parser(
			MappingParser(
				os.path.join('Database', 'report', "report_ej.txt"),
				{
					'EJ_Person_Trips_HWej_Tot_Per_Trips':            key['EJ_Person_Trips.HWej_Tot_Per_Trips'           ],
					'EJ_Person_Trips_HWej_Aut_Per_Trips':            key['EJ_Person_Trips.HWej_Aut_Per_Trips'           ],
					'EJ_Person_Trips_HWej_Tra_Per_Trips':            key['EJ_Person_Trips.HWej_Tra_Per_Trips'           ],
					'EJ_Person_Trips_HOej_Tot_Per_Trips':            key['EJ_Person_Trips.HOej_Tot_Per_Trips'           ],
					'EJ_Person_Trips_HOej_Aut_Per_Trips':            key['EJ_Person_Trips.HOej_Aut_Per_Trips'           ],
					'EJ_Person_Trips_HOej_Tra_Per_Trips':            key['EJ_Person_Trips.HOej_Tra_Per_Trips'           ],
					'EJ_Person_Trips_NHej_Tot_Per_Trips':            key['EJ_Person_Trips.NHej_Tot_Per_Trips'           ],
					'EJ_Person_Trips_NHej_Aut_Per_Trips':            key['EJ_Person_Trips.NHej_Aut_Per_Trips'           ],
					'EJ_Person_Trips_NHej_Tra_Per_Trips':            key['EJ_Person_Trips.NHej_Tra_Per_Trips'           ],
					'EJ_Transit_Share_HWej_Tran_Shr':                key['EJ_Transit_Share.HWej_Tran_Shr'               ],
					'EJ_Transit_Share_HOej_Tran_Shr':                key['EJ_Transit_Share.HOej_Tran_Shr'               ],
					'EJ_Transit_Share_NHej_Tran_Shr':                key['EJ_Transit_Share.NHej_Tran_Shr'               ],
					'EJ_Transit_Share_Totej_Tran_Shr':               key['EJ_Transit_Share.Totej_Tran_Shr'              ],
					'EJ_Average_Trip_Time_HWej_Aut_Avg_Min':         key['EJ_Average_Trip_Time_.HWej_Aut_Avg_Min'       ],
					'EJ_Average_Trip_Time_HOej_Aut_Avg_Min':         key['EJ_Average_Trip_Time_.HOej_Aut_Avg_Min'       ],
					'EJ_Average_Trip_Time_NHej_Aut_Avg_Min':         key['EJ_Average_Trip_Time_.NHej_Aut_Avg_Min'       ],
					'Average_EJ_TRANSIT_Trip_Time_HWej_Trn_Avg_Min': key['Average_EJ_TRANSIT_Trip_Time.HWej_Trn_Avg_Min'],
					'Average_EJ_TRANSIT_Trip_Time_HOej_Trn_Avg_Min': key['Average_EJ_TRANSIT_Trip_Time.HOej_Trn_Avg_Min'],
					'Average_EJ_TRANSIT_Trip_Time_NHej_trn_Avg_Min': key['Average_EJ_TRANSIT_Trip_Time.NHej_trn_Avg_Min'],
				},
				reader_method=tiered_file_parse_space,
			)
		)

		self.add_parser(
			MappingParser(
				os.path.join('Database', 'report', "interchange_times.txt"),
				{
					 'mf44_amtime_311_to_24'    :  key['mf44_amtime_311_to_24'   ],
					 'mf44_amtime_311_to_125'   :  key['mf44_amtime_311_to_125'  ],
					 'mf44_amtime_311_to_511'   :  key['mf44_amtime_311_to_511'  ],
					 'mf44_amtime_311_to_2049'  :  key['mf44_amtime_311_to_2049' ],
					 'mf44_amtime_384_to_24'    :  key['mf44_amtime_384_to_24'   ],
					 'mf44_amtime_384_to_125'   :  key['mf44_amtime_384_to_125'  ],
					 'mf44_amtime_384_to_511'   :  key['mf44_amtime_384_to_511'  ],
					 'mf44_amtime_384_to_2049'  :  key['mf44_amtime_384_to_2049' ],
					 'mf44_amtime_623_to_24'    :  key['mf44_amtime_623_to_24'   ],
					 'mf44_amtime_623_to_125'   :  key['mf44_amtime_623_to_125'  ],
					 'mf44_amtime_623_to_511'   :  key['mf44_amtime_623_to_511'  ],
					 'mf44_amtime_623_to_2049'  :  key['mf44_amtime_623_to_2049' ],
					 'mf44_amtime_1636_to_24'   :  key['mf44_amtime_1636_to_24'  ],
					 'mf44_amtime_1636_to_125'  :  key['mf44_amtime_1636_to_125' ],
					 'mf44_amtime_1636_to_511'  :  key['mf44_amtime_1636_to_511' ],
					 'mf44_amtime_1636_to_2049' :  key['mf44_amtime_1636_to_2049'],
					 'mf44_amtime_2004_to_24'   :  key['mf44_amtime_2004_to_24'  ],
					 'mf44_amtime_2004_to_125'  :  key['mf44_amtime_2004_to_125' ],
					 'mf44_amtime_2004_to_511'  :  key['mf44_amtime_2004_to_511' ],
					 'mf44_amtime_2004_to_2049' :  key['mf44_amtime_2004_to_2049'],
					 'mf44_amtime_2203_to_24'   :  key['mf44_amtime_2203_to_24'  ],
					 'mf44_amtime_2203_to_125'  :  key['mf44_amtime_2203_to_125' ],
					 'mf44_amtime_2203_to_511'  :  key['mf44_amtime_2203_to_511' ],
					 'mf44_amtime_2203_to_2049' :  key['mf44_amtime_2203_to_2049'],
					 'mf44_amtime_2290_to_24'   :  key['mf44_amtime_2290_to_24'  ],
					 'mf44_amtime_2290_to_125'  :  key['mf44_amtime_2290_to_125' ],
					 'mf44_amtime_2290_to_511'  :  key['mf44_amtime_2290_to_511' ],
					 'mf44_amtime_2290_to_2049' :  key['mf44_amtime_2290_to_2049'],
					 'mf44_amtime_2507_to_24'   :  key['mf44_amtime_2507_to_24'  ],
					 'mf44_amtime_2507_to_125'  :  key['mf44_amtime_2507_to_125' ],
					 'mf44_amtime_2507_to_511'  :  key['mf44_amtime_2507_to_511' ],
					 'mf44_amtime_2507_to_2049' :  key['mf44_amtime_2507_to_2049'],
					 'mf44_amtime_2796_to_24'   :  key['mf44_amtime_2796_to_24'  ],
					 'mf44_amtime_2796_to_125'  :  key['mf44_amtime_2796_to_125' ],
					 'mf44_amtime_2796_to_511'  :  key['mf44_amtime_2796_to_511' ],
					 'mf44_amtime_2796_to_2049' :  key['mf44_amtime_2796_to_2049'],
					 'mf45_amdist_311_to_24'    :  key['mf45_amdist_311_to_24'   ],
					 'mf45_amdist_311_to_125'   :  key['mf45_amdist_311_to_125'  ],
					 'mf45_amdist_311_to_511'   :  key['mf45_amdist_311_to_511'  ],
					 'mf45_amdist_311_to_2049'  :  key['mf45_amdist_311_to_2049' ],
					 'mf45_amdist_384_to_24'    :  key['mf45_amdist_384_to_24'   ],
					 'mf45_amdist_384_to_125'   :  key['mf45_amdist_384_to_125'  ],
					 'mf45_amdist_384_to_511'   :  key['mf45_amdist_384_to_511'  ],
					 'mf45_amdist_384_to_2049'  :  key['mf45_amdist_384_to_2049' ],
					 'mf45_amdist_623_to_24'    :  key['mf45_amdist_623_to_24'   ],
					 'mf45_amdist_623_to_125'   :  key['mf45_amdist_623_to_125'  ],
					 'mf45_amdist_623_to_511'   :  key['mf45_amdist_623_to_511'  ],
					 'mf45_amdist_623_to_2049'  :  key['mf45_amdist_623_to_2049' ],
					 'mf45_amdist_1636_to_24'   :  key['mf45_amdist_1636_to_24'  ],
					 'mf45_amdist_1636_to_125'  :  key['mf45_amdist_1636_to_125' ],
					 'mf45_amdist_1636_to_511'  :  key['mf45_amdist_1636_to_511' ],
					 'mf45_amdist_1636_to_2049' :  key['mf45_amdist_1636_to_2049'],
					 'mf45_amdist_2004_to_24'   :  key['mf45_amdist_2004_to_24'  ],
					 'mf45_amdist_2004_to_125'  :  key['mf45_amdist_2004_to_125' ],
					 'mf45_amdist_2004_to_511'  :  key['mf45_amdist_2004_to_511' ],
					 'mf45_amdist_2004_to_2049' :  key['mf45_amdist_2004_to_2049'],
					 'mf45_amdist_2203_to_24'   :  key['mf45_amdist_2203_to_24'  ],
					 'mf45_amdist_2203_to_125'  :  key['mf45_amdist_2203_to_125' ],
					 'mf45_amdist_2203_to_511'  :  key['mf45_amdist_2203_to_511' ],
					 'mf45_amdist_2203_to_2049' :  key['mf45_amdist_2203_to_2049'],
					 'mf45_amdist_2290_to_24'   :  key['mf45_amdist_2290_to_24'  ],
					 'mf45_amdist_2290_to_125'  :  key['mf45_amdist_2290_to_125' ],
					 'mf45_amdist_2290_to_511'  :  key['mf45_amdist_2290_to_511' ],
					 'mf45_amdist_2290_to_2049' :  key['mf45_amdist_2290_to_2049'],
					 'mf45_amdist_2507_to_24'   :  key['mf45_amdist_2507_to_24'  ],
					 'mf45_amdist_2507_to_125'  :  key['mf45_amdist_2507_to_125' ],
					 'mf45_amdist_2507_to_511'  :  key['mf45_amdist_2507_to_511' ],
					 'mf45_amdist_2507_to_2049' :  key['mf45_amdist_2507_to_2049'],
					 'mf45_amdist_2796_to_24'   :  key['mf45_amdist_2796_to_24'  ],
					 'mf45_amdist_2796_to_125'  :  key['mf45_amdist_2796_to_125' ],
					 'mf45_amdist_2796_to_511'  :  key['mf45_amdist_2796_to_511' ],
					 'mf45_amdist_2796_to_2049' :  key['mf45_amdist_2796_to_2049'],
					 'mf46_mdtime_311_to_24'    :  key['mf46_mdtime_311_to_24'   ],
					 'mf46_mdtime_311_to_125'   :  key['mf46_mdtime_311_to_125'  ],
					 'mf46_mdtime_311_to_511'   :  key['mf46_mdtime_311_to_511'  ],
					 'mf46_mdtime_311_to_2049'  :  key['mf46_mdtime_311_to_2049' ],
					 'mf46_mdtime_384_to_24'    :  key['mf46_mdtime_384_to_24'   ],
					 'mf46_mdtime_384_to_125'   :  key['mf46_mdtime_384_to_125'  ],
					 'mf46_mdtime_384_to_511'   :  key['mf46_mdtime_384_to_511'  ],
					 'mf46_mdtime_384_to_2049'  :  key['mf46_mdtime_384_to_2049' ],
					 'mf46_mdtime_623_to_24'    :  key['mf46_mdtime_623_to_24'   ],
					 'mf46_mdtime_623_to_125'   :  key['mf46_mdtime_623_to_125'  ],
					 'mf46_mdtime_623_to_511'   :  key['mf46_mdtime_623_to_511'  ],
					 'mf46_mdtime_623_to_2049'  :  key['mf46_mdtime_623_to_2049' ],
					 'mf46_mdtime_1636_to_24'   :  key['mf46_mdtime_1636_to_24'  ],
					 'mf46_mdtime_1636_to_125'  :  key['mf46_mdtime_1636_to_125' ],
					 'mf46_mdtime_1636_to_511'  :  key['mf46_mdtime_1636_to_511' ],
					 'mf46_mdtime_1636_to_2049' :  key['mf46_mdtime_1636_to_2049'],
					 'mf46_mdtime_2004_to_24'   :  key['mf46_mdtime_2004_to_24'  ],
					 'mf46_mdtime_2004_to_125'  :  key['mf46_mdtime_2004_to_125' ],
					 'mf46_mdtime_2004_to_511'  :  key['mf46_mdtime_2004_to_511' ],
					 'mf46_mdtime_2004_to_2049' :  key['mf46_mdtime_2004_to_2049'],
					 'mf46_mdtime_2203_to_24'   :  key['mf46_mdtime_2203_to_24'  ],
					 'mf46_mdtime_2203_to_125'  :  key['mf46_mdtime_2203_to_125' ],
					 'mf46_mdtime_2203_to_511'  :  key['mf46_mdtime_2203_to_511' ],
					 'mf46_mdtime_2203_to_2049' :  key['mf46_mdtime_2203_to_2049'],
					 'mf46_mdtime_2290_to_24'   :  key['mf46_mdtime_2290_to_24'  ],
					 'mf46_mdtime_2290_to_125'  :  key['mf46_mdtime_2290_to_125' ],
					 'mf46_mdtime_2290_to_511'  :  key['mf46_mdtime_2290_to_511' ],
					 'mf46_mdtime_2290_to_2049' :  key['mf46_mdtime_2290_to_2049'],
					 'mf46_mdtime_2507_to_24'   :  key['mf46_mdtime_2507_to_24'  ],
					 'mf46_mdtime_2507_to_125'  :  key['mf46_mdtime_2507_to_125' ],
					 'mf46_mdtime_2507_to_511'  :  key['mf46_mdtime_2507_to_511' ],
					 'mf46_mdtime_2507_to_2049' :  key['mf46_mdtime_2507_to_2049'],
					 'mf46_mdtime_2796_to_24'   :  key['mf46_mdtime_2796_to_24'  ],
					 'mf46_mdtime_2796_to_125'  :  key['mf46_mdtime_2796_to_125' ],
					 'mf46_mdtime_2796_to_511'  :  key['mf46_mdtime_2796_to_511' ],
					 'mf46_mdtime_2796_to_2049' :  key['mf46_mdtime_2796_to_2049'],
					 'mf47_mddist_311_to_24'    :  key['mf47_mddist_311_to_24'   ],
					 'mf47_mddist_311_to_125'   :  key['mf47_mddist_311_to_125'  ],
					 'mf47_mddist_311_to_511'   :  key['mf47_mddist_311_to_511'  ],
					 'mf47_mddist_311_to_2049'  :  key['mf47_mddist_311_to_2049' ],
					 'mf47_mddist_384_to_24'    :  key['mf47_mddist_384_to_24'   ],
					 'mf47_mddist_384_to_125'   :  key['mf47_mddist_384_to_125'  ],
					 'mf47_mddist_384_to_511'   :  key['mf47_mddist_384_to_511'  ],
					 'mf47_mddist_384_to_2049'  :  key['mf47_mddist_384_to_2049' ],
					 'mf47_mddist_623_to_24'    :  key['mf47_mddist_623_to_24'   ],
					 'mf47_mddist_623_to_125'   :  key['mf47_mddist_623_to_125'  ],
					 'mf47_mddist_623_to_511'   :  key['mf47_mddist_623_to_511'  ],
					 'mf47_mddist_623_to_2049'  :  key['mf47_mddist_623_to_2049' ],
					 'mf47_mddist_1636_to_24'   :  key['mf47_mddist_1636_to_24'  ],
					 'mf47_mddist_1636_to_125'  :  key['mf47_mddist_1636_to_125' ],
					 'mf47_mddist_1636_to_511'  :  key['mf47_mddist_1636_to_511' ],
					 'mf47_mddist_1636_to_2049' :  key['mf47_mddist_1636_to_2049'],
					 'mf47_mddist_2004_to_24'   :  key['mf47_mddist_2004_to_24'  ],
					 'mf47_mddist_2004_to_125'  :  key['mf47_mddist_2004_to_125' ],
					 'mf47_mddist_2004_to_511'  :  key['mf47_mddist_2004_to_511' ],
					 'mf47_mddist_2004_to_2049' :  key['mf47_mddist_2004_to_2049'],
					 'mf47_mddist_2203_to_24'   :  key['mf47_mddist_2203_to_24'  ],
					 'mf47_mddist_2203_to_125'  :  key['mf47_mddist_2203_to_125' ],
					 'mf47_mddist_2203_to_511'  :  key['mf47_mddist_2203_to_511' ],
					 'mf47_mddist_2203_to_2049' :  key['mf47_mddist_2203_to_2049'],
					 'mf47_mddist_2290_to_24'   :  key['mf47_mddist_2290_to_24'  ],
					 'mf47_mddist_2290_to_125'  :  key['mf47_mddist_2290_to_125' ],
					 'mf47_mddist_2290_to_511'  :  key['mf47_mddist_2290_to_511' ],
					 'mf47_mddist_2290_to_2049' :  key['mf47_mddist_2290_to_2049'],
					 'mf47_mddist_2507_to_24'   :  key['mf47_mddist_2507_to_24'  ],
					 'mf47_mddist_2507_to_125'  :  key['mf47_mddist_2507_to_125' ],
					 'mf47_mddist_2507_to_511'  :  key['mf47_mddist_2507_to_511' ],
					 'mf47_mddist_2507_to_2049' :  key['mf47_mddist_2507_to_2049'],
					 'mf47_mddist_2796_to_24'   :  key['mf47_mddist_2796_to_24'  ],
					 'mf47_mddist_2796_to_125'  :  key['mf47_mddist_2796_to_125' ],
					 'mf47_mddist_2796_to_511'  :  key['mf47_mddist_2796_to_511' ],
					 'mf47_mddist_2796_to_2049' :  key['mf47_mddist_2796_to_2049'],
				},
				reader_method=interchange_file_parse,
			)
		)

		# self.add_parser(
		# 	TableParser(
		# 		os.path.join('Database', "transit_report_100_nonwork.txt"),
		# 		{
		# 			'NonWork_Pace_Board':    loc['Pace_Board',1],
		# 			'NonWork_Pace_PMT':      loc['Pace_PMT',1],
		# 			'NonWork_Pace_PHT':      loc['Pace_PHT',1],
		# 			'NonWork_CTA_Bus_Board': loc['CTA_Bus_Board',1],
		# 			'NonWork_CTA_Bus_PMT':   loc['CTA_Bus_PMT',1],
		# 			'NonWork_CTA_Bus_PHT':   loc['CTA_Bus_PHT',1],
		# 			'NonWork_CTA_Rail_Board':loc['CTA_Rail_Board',1],
		# 			'NonWork_CTA_Rail_PMT':  loc['CTA_Rail_PMT',1],
		# 			'NonWork_CTA_Rail_PHT':  loc['CTA_Rail_PHT',1],
		# 			'NonWork_Metra_Board':   loc['Metra_Board',1],
		# 			'NonWork_Metra_PMT':     loc['Metra_PMT',1],
		# 			'NonWork_Metra_PHT':     loc['Metra_PHT',1],
		# 			'NonWork_Pace_SegMi':    loc['Pace_SegMi',1],
		# 			'NonWork_CTA_BUS_SeqMi': loc['CTA_BUS_SeqMi',1],
		# 			'NonWork_CTA_Rail_SegMi':loc['CTA_Rail_SegMi',1],
		# 			'NonWork_Metra_SegMi':   loc['Metra_SegMi',1],
		# 			'NonWork_Work_Auto':     loc['Work_Auto',1],
		# 			'NonWork_Nonwork_Auto':  loc['Nonwork_Auto',1],
		# 			'NonWork_Work_Tran':     loc['Work_Tran',1],
		# 			'NonWork_Nonwork_tran':  loc['Nonwork_tran',1],
		# 			'NonWork_Trucks_tot':    loc['Trucks_tot',1],
		# 			'NonWork_Blue_Board':    loc['Blue_Board',1],
		# 			'NonWork_Brown_Board':   loc['Brown_Board',1],
		# 			'NonWork_Green_Board':   loc['Green_Board',1],
		# 			'NonWork_Red_Board':     loc['Red_Board',1],
		# 			'NonWork_Yellow_Board':  loc['Yellow_Board',1],
		# 			'NonWork_Purple_Board':  loc['Purple_Board',1],
		# 			'NonWork_Pink_Board':    loc['Pink_Board',1],
		# 			'NonWork_Orange_Board':  loc['Orange_Board',1],
		# 			'NonWork_Blue_PMT':      loc['Blue_PMT',1],
		# 			'NonWork_Brown_PMT':     loc['Brown_PMT',1],
		# 			'NonWork_Green_PMT':     loc['Green_PMT',1],
		# 			'NonWork_Red_PMT':       loc['Red_PMT',1],
		# 			'NonWork_Yellow_PMT':    loc['Yellow_PMT',1],
		# 			'NonWork_Purple_PMT':    loc['Purple_PMT',1],
		# 			'NonWork_Pink_PMT':      loc['Pink_PMT',1],
		# 			'NonWork_Orange_PMT':    loc['Orange_PMT',1],
		# 			'NonWork_BNSF_Board':    loc['BNSF_Board',1],
		# 			'NonWork_Hert_Board':    loc['Hert_Board',1],
		# 			'NonWork_MED_Board':     loc['MED_Board',1],
		# 			'NonWork_MD_N_Board':    loc['MD-N_Board',1],
		# 			'NonWork_MDW_Board':     loc['MDW_Board',1],
		# 			'NonWork_NCS_Board':     loc['NCS_Board',1],
		# 			'NonWork_UP_NW_Board':   loc['UP-NW_Board',1],
		# 			'NonWork_RID_Board':     loc['RID_Board',1],
		# 			'NonWork_CSSSB_Board':   loc['CSSSB_Board',1],
		# 			'NonWork_SWS_Board':     loc['SWS_Board',1],
		# 			'NonWork_UP_N_Board':    loc['UP-N_Board',1],
		# 			'NonWork_UP_W_Board':    loc['UP-W_Board',1],
		# 			'NonWork_BNSF_PMT':      loc['BNSF_PMT',1],
		# 			'NonWork_Hert_PMT':      loc['Hert_PMT',1],
		# 			'NonWork_MED_PMT':       loc['MED_PMT',1],
		# 			'NonWork_MD_N_PMT':      loc['MD-N_PMT',1],
		# 			'NonWork_MDW_PMT':       loc['MDW_PMT',1],
		# 			'NonWork_NCS_PMT':       loc['NCS_PMT',1],
		# 			'NonWork_UP_NW_PMT':     loc['UP-NW_PMT',1],
		# 			'NonWork_RID_PMT':       loc['RID_PMT',1],
		# 			'NonWork_CSSSB_PMT':     loc['CSSSB_PMT',1],
		# 			'NonWork_SWS_PMT':       loc['SWS_PMT',1],
		# 			'NonWork_UP_N_PMT':      loc['UP-N_PMT',1],
		# 			'NonWork_UP_W_PMT':      loc['UP-W_PMT',1],
		# 		},
		# 		sep=r"\s+",
		# 		header=None,
		# 		index_col=0,
		# 		na_values=['--'],
		# 	)
		# )
		#
		# self.add_parser(
		# 	TableParser(
		# 		os.path.join('Database', "transit_report_100_work.txt"),
		# 		{
		# 			'Work_Pace_Board':    loc['Pace_Board',1],
		# 			'Work_Pace_PMT':      loc['Pace_PMT',1],
		# 			'Work_Pace_PHT':      loc['Pace_PHT',1],
		# 			'Work_CTA_Bus_Board': loc['CTA_Bus_Board',1],
		# 			'Work_CTA_Bus_PMT':   loc['CTA_Bus_PMT',1],
		# 			'Work_CTA_Bus_PHT':   loc['CTA_Bus_PHT',1],
		# 			'Work_CTA_Rail_Board':loc['CTA_Rail_Board',1],
		# 			'Work_CTA_Rail_PMT':  loc['CTA_Rail_PMT',1],
		# 			'Work_CTA_Rail_PHT':  loc['CTA_Rail_PHT',1],
		# 			'Work_Metra_Board':   loc['Metra_Board',1],
		# 			'Work_Metra_PMT':     loc['Metra_PMT',1],
		# 			'Work_Metra_PHT':     loc['Metra_PHT',1],
		# 			'Work_Pace_SegMi':    loc['Pace_SegMi',1],
		# 			'Work_CTA_BUS_SeqMi': loc['CTA_BUS_SeqMi',1],
		# 			'Work_CTA_Rail_SegMi':loc['CTA_Rail_SegMi',1],
		# 			'Work_Metra_SegMi':   loc['Metra_SegMi',1],
		# 			'Work_Work_Auto':     loc['Work_Auto',1],
		# 			'Work_Nonwork_Auto':  loc['Nonwork_Auto',1],
		# 			'Work_Work_Tran':     loc['Work_Tran',1],
		# 			'Work_Nonwork_tran':  loc['Nonwork_tran',1],
		# 			'Work_Trucks_tot':    loc['Trucks_tot',1],
		# 			'Work_Blue_Board':    loc['Blue_Board',1],
		# 			'Work_Brown_Board':   loc['Brown_Board',1],
		# 			'Work_Green_Board':   loc['Green_Board',1],
		# 			'Work_Red_Board':     loc['Red_Board',1],
		# 			'Work_Yellow_Board':  loc['Yellow_Board',1],
		# 			'Work_Purple_Board':  loc['Purple_Board',1],
		# 			'Work_Pink_Board':    loc['Pink_Board',1],
		# 			'Work_Orange_Board':  loc['Orange_Board',1],
		# 			'Work_Blue_PMT':      loc['Blue_PMT',1],
		# 			'Work_Brown_PMT':     loc['Brown_PMT',1],
		# 			'Work_Green_PMT':     loc['Green_PMT',1],
		# 			'Work_Red_PMT':       loc['Red_PMT',1],
		# 			'Work_Yellow_PMT':    loc['Yellow_PMT',1],
		# 			'Work_Purple_PMT':    loc['Purple_PMT',1],
		# 			'Work_Pink_PMT':      loc['Pink_PMT',1],
		# 			'Work_Orange_PMT':    loc['Orange_PMT',1],
		# 			'Work_BNSF_Board':    loc['BNSF_Board',1],
		# 			'Work_Hert_Board':    loc['Hert_Board',1],
		# 			'Work_MED_Board':     loc['MED_Board',1],
		# 			'Work_MD_N_Board':    loc['MD-N_Board',1],
		# 			'Work_MDW_Board':     loc['MDW_Board',1],
		# 			'Work_NCS_Board':     loc['NCS_Board',1],
		# 			'Work_UP_NW_Board':   loc['UP-NW_Board',1],
		# 			'Work_RID_Board':     loc['RID_Board',1],
		# 			'Work_CSSSB_Board':   loc['CSSSB_Board',1],
		# 			'Work_SWS_Board':     loc['SWS_Board',1],
		# 			'Work_UP_N_Board':    loc['UP-N_Board',1],
		# 			'Work_UP_W_Board':    loc['UP-W_Board',1],
		# 			'Work_BNSF_PMT':      loc['BNSF_PMT',1],
		# 			'Work_Hert_PMT':      loc['Hert_PMT',1],
		# 			'Work_MED_PMT':       loc['MED_PMT',1],
		# 			'Work_MD_N_PMT':      loc['MD-N_PMT',1],
		# 			'Work_MDW_PMT':       loc['MDW_PMT',1],
		# 			'Work_NCS_PMT':       loc['NCS_PMT',1],
		# 			'Work_UP_NW_PMT':     loc['UP-NW_PMT',1],
		# 			'Work_RID_PMT':       loc['RID_PMT',1],
		# 			'Work_CSSSB_PMT':     loc['CSSSB_PMT',1],
		# 			'Work_SWS_PMT':       loc['SWS_PMT',1],
		# 			'Work_UP_N_PMT':      loc['UP-N_PMT',1],
		# 			'Work_UP_W_PMT':      loc['UP-W_PMT',1],
		# 		},
		# 		sep=r"\s+",
		# 		header=None,
		# 		index_col=0,
		# 		na_values=['--'],
		# 	)
		# )

		_logger.info("CMAP EMAT Model INIT complete.")


	def setup(self, params: dict):
		"""
		Configure the demo core model with the experiment variable values.

		This method is the place where the core model set up takes place,
		including creating or modifying files as necessary to prepare
		for a core model run.  When running experiments, this method
		is called once for each core model experiment, where each experiment
		is defined by a set of particular values for both the exogenous
		uncertainties and the policy levers.  These values are passed to
		the experiment only here, and not in the `run` method itself.
		This facilitates debugging, as the `setup` method can potentially
		be used without the `run` method, allowing the user to manually
		inspect the prepared files and ensure they are correct before
		actually running a potentially expensive model.

		Each input exogenous uncertainty or policy lever can potentially
		be used to manipulate multiple different aspects of the underlying
		core model.  For example, a policy lever that includes a number of
		discrete future network "build" options might trigger the replacement
		of multiple related network definition files.  Or, a single uncertainty
		relating to the cost of fuel might scale both a parameter linked to
		the modeled per-mile cost of operating an automobile, as well as the
		modeled total cost of fuel used by transit services.

		At the end of the `setup` method, a core model experiment should be
		ready to run using the `run` method.

		Args:
			params (dict):
				experiment variables including both exogenous
				uncertainty and policy levers

		Raises:
			KeyError:
				if a defined experiment variable is not supported
				by the core model
				:param params:
				:param self:
		"""
		_logger.info("CMAP EMAT Model SETUP...")

		# Use provided unique ID or create one.
		if self.unique_id is None:
			self.uid = str(uuid())
		else:
			self.uid = f"{self.unique_id}-{str(uuid())}"

		if self.ephemeral:
			_logger.debug("using an ephemeral copy of model files")
			# Copy the model to a temp directory
			self.temporary_directory = tempfile.TemporaryDirectory()
			self.model_copy_path = self.temporary_directory.name
		else:
			_logger.debug("using a stable copy of model files")
			# Copy the model to a working directory next to the original
			copy_name = f"{os.path.basename(self.source_model_path.replace('_Clean',''))}-{self.uid}"
			self.model_copy_path = os.path.normpath(
				os.path.join(self.source_model_path, '..', copy_name)
			)
		if params['land_use'] == 'base':
			source_model_path_1 = join_norm(self.source_model_path, self.config['model_path_land_use_base'])
		else:
			source_model_path_1 = join_norm(self.source_model_path, self.config['model_path_land_use_alt1'])
		_logger.info(f"copying from: {source_model_path_1}")
		_logger.info(f"copying to: {self.model_copy_path}")
		copy_tree(
			source_model_path_1,
			self.model_copy_path,
			update=True,
		)
		_logger.info(f"copying complete")

		# Write params and experiment_id to folder, if possible
		try:
			try:
				import yaml as serializer
			except ImportError:
				import json as serializer
			simple_params = {k:to_simple_python(v) for k,v in params.items()}
			with open(join_norm(self.model_copy_path,"_emat_parameters_.yml"), 'w') as fstream:
				serializer.dump(simple_params, fstream)
			db = getattr(self, 'db', None)
			if db is not None:
				experiment_id = db.get_experiment_id(self.scope.name, None, params)
				with open(join_norm(self.model_copy_path,"_emat_experiment_id_.yml"), 'w') as fstream:
					serializer.dump({
						'experiment_id':experiment_id,
						'uid':self.uid,
					}, fstream)
		except:
			pass

		self.model_path = os.path.abspath(self.model_copy_path)

		# call any other functions to set up EMME init file and copy correct network/land use files
		self._manipulate_EMME_init(params)
		self._manipulate_cost_input_files(params)    #uses fuel_cost; fuel_economy; vmt_charge
		self._manipulate_transit_skimming(params)    #uses transit_fares
		self._manipulate_transit_assignment(params)  #uses transit_fares
		self._manipulate_batch_file(params)

		_logger.info("CMAP EMAT RUN SETUP complete")

	def _manipulate_batch_file(self, params):

		# Now, we load the text of the EMME initialization macro template into a string in memory
		with open(template('EMAT_Submit_Full_Regional_Model.template'), 'rt') as f:
			y = f.read()

		if params['global_loops'] > 4:
			raise ValueError("CMAP model will crash if global loops set greater than 4")

		y = y.replace(
			f"__EMAT_PROVIDES_GLOBAL_LOOPS__",  # the token to replace
			str(params['global_loops'])  # the value to replace it with (as a string)
		)

		# Write the manipulated text back out to model run folder.  We don't write
		# to the template file, but to the expected normal filename for our script.
		macro_filename = join_norm(
			self.resolved_model_path,
			'Database', 'EMAT_Submit_Full_Regional_Model.bat'
		)
		_logger.debug(f"writing updates to: {macro_filename}")
		with open(macro_filename, 'wt') as f:
			f.write(y)

	def _manipulate_EMME_init (self, params):

		# the `params` dictionary here will have keys corresponding to cmap-trip-scope, so:
		#     - highway_cap
		#     - park_price
		#     - transit_fares
		#     - telecommuting
		#     - fuel_cost;fuel_economy;vmt_charge (not here)
		#     - expressway_toll
		#     - vot_sensitivity
		#     - transit_fares (not here)

		# The initialization macro template we will manipulate contains 8 unique tokens that
		# we will need to replace in the file, and they are listed here.
		tokens_in_file = [
			'__parking__pricing__factor__', # line 21
			'__transit__fare__factor__', # line 23
			'__work__from__home__percent__', # line 25
			'__ivt__sensitivity__factor__', # line 27
			'__vot__assign__work__', # line 29
			'__vot__assign__nonwork__', # line 31
			'__highway__capacity__factor__for__cav__', # line 33
			'__freeway_toll_rate__', # line 35
		]

		computed_params = params.copy()
		computed_params['__parking__pricing__factor__']            = 1    * params['park_price']
		computed_params['__transit__fare__factor__'] 	           = 1    * params['transit_fares']
		computed_params['__work__from__home__percent__']           = 1    * params['telecommuting']
		computed_params['__ivt__sensitivity__factor__']            = 1    * params['vot_sensitivity']
		computed_params['__vot__assign__work__']                   = 0.53 / params['vot_sensitivity']
		computed_params['__vot__assign__nonwork__']                = 0.38 / params['vot_sensitivity']
		computed_params['__highway__capacity__factor__for__cav__'] = 1    * params['highway_cap']
		computed_params['__freeway_toll_rate__']                   = 1    * params['expressway_toll']

		# Now, we load the text of the EMME initialization macro template into a string in memory
		with open(template('initialize_EMAT_variables.template'), 'rt') as f:
			y = f.read()

		# Loop over all the tokens in the file, replacing them with usable values,
		# or triggering an error if we cannot.
		for n in tokens_in_file:
			if n in computed_params:
				# No regex here, just a simple string replacement.  Note the replacement
				# value also must itself be a string.
				y = y.replace(
					f"__EMAT_PROVIDES{n}",  # the token to replace
					str(computed_params[n])  # the value to replace it with (as a string)
				)
			else:
				# Raise an error now if one of the required parameters is missing, to
				# save us the trouble of having the error crop up later, because it will.
				raise ValueError(f'missing required parameter "{n}"')

		# Write the manipulated text back out to model run folder.  We don't write
		# to the template file, but to the expected normal filename for our script.
		macro_filename = join_norm(
			self.resolved_model_path,
			'Database','prep_macros','initialize_EMAT_variables.mac'
		)
		_logger.debug(f"writing updates to: {macro_filename}")
		with open(macro_filename, 'wt') as f:
			f.write(y)

	def peak_tolled_auto_operating_cost(self, params):
		fuel_cost_within_range = ((params['fuel_cost'] - 2.5) / (6.0-2.5))
		fuel_economy_min = 0.6
		fuel_economy_max = 0.8
		fuel_economy = fuel_economy_min + (fuel_economy_max-fuel_economy_min)*(1.0-fuel_cost_within_range)
		peak = int(np.round(10000 * (params['fuel_cost']/((1/0.0309) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		return peak

	def _manipulate_cost_input_files (self, params):
		# There are 8 values in this file that need to be edited.
		tokens_in_file = [
			'__auto__opt__cost__p1__',
			'__auto__opt__cost__p2__',
			'__auto__opt__cost__p3__',
			'__auto__opt__cost__p4__',
			'__auto__opt__cost__p5__',
			'__auto__opt__cost__p6__',
			'__auto__opt__cost__p7__',
			'__auto__opt__cost__p8__',
			'__auto__opt__cost__p9__',
			'__auto__opt__cost__p10__',
			'__auto__opt__cost__p11__',
			'__auto__opt__cost__p12__',
			'__auto__opt__cost__p13__',
			'__auto__opt__cost__p14__',
			'__auto__opt__cost__p15__',
			'__auto__opt__cost__p16__',
		]
		# They need to be revised into:
		computed_params = params.copy()

		# This replaces the perfect negative correlation for fuel economy with fuel cost
		fuel_cost_within_range = ((params['fuel_cost'] - 2.5) / (6.0-2.5))
		fuel_economy_min = 0.6
		fuel_economy_max = 0.8
		fuel_economy = fuel_economy_min + (fuel_economy_max-fuel_economy_min)*(1.0-fuel_cost_within_range)

		# Opcost_s = [[Fuel Cost]* 1 / ( [1 / inv_fuel_econ_s] * [1 + Fuel Economy Increase] ) ] + [Fixed Tires & Maint] + [VMT Charge]
		computed_params['__auto__opt__cost__p1__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0639) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p2__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0522) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p3__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0442) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p4__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0382) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p5__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0342) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p6__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0322) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p7__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0318) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p8__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0322) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p9__']  = int(np.round(10000 * (params['fuel_cost']/((1/0.0319) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p10__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0313) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p11__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0309) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p12__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0313) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p13__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0330) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p14__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0357) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p15__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0388) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))
		computed_params['__auto__opt__cost__p16__'] = int(np.round(10000 * (params['fuel_cost']/((1/0.0424) * (1 + fuel_economy)) + 0.0533 + params['vmt_charge'])))

		base_templates = [
			'MCHO_M023',
			'MCHW_M023',
			'MCNH_M023',
			'PDHO_M023',
			'PDHW_M023',
			'PDNH_M023',
		]

		for base_template in base_templates:
			# Now, we load the text of the template into a string in memory
			with open(template(f'{base_template}.template'), 'rt') as f:
				y = f.read()

			for n in tokens_in_file:
				if n in computed_params:
					y = y.replace(
						f"__EMAT_PROVIDES{n}",  # the token to replace
						str(computed_params[n])  # the value to replace it with (as a string)
					)
				else:
					raise ValueError(f'missing required parameter "{n}"')
			# Write the manipulated text back out to model run folder.
			macro_filename = join_norm(
				self.resolved_model_path,
				'Database', f'{base_template}.txt'
			)
			_logger.debug(f"writing updates to: {macro_filename}")
			with open(macro_filename, 'wt') as f:
				f.write(y)


	def  _manipulate_transit_skimming (self, params):
		# There are 4 values in this file that need to be edited.
		tokens_in_file = [
			'__base__fare__cta__', # line 119 -122
			'__base__fare__pace__',
			'__base__fare__metra__',
			'__base__fare__trans__',
		]
		# They need to be revised into:
		computed_params = params.copy()
		computed_params['__base__fare__cta__']   = int(np.round( 150 * params['transit_fares']))
		computed_params['__base__fare__pace__']  = int(np.round( 150 * params['transit_fares']))
		computed_params['__base__fare__metra__'] = int(np.round( 136 * params['transit_fares']))
		computed_params['__base__fare__trans__'] = int(np.round(-120 * params['transit_fares']))

		with open(template('skim.transit.all.template'), 'rt') as f:
			y = f.read()

		for n in tokens_in_file:
			if n in computed_params:
				y = y.replace(
					f"__EMAT_PROVIDES{n}",  # the token to replace
					str(computed_params[n])  # the value to replace it with (as a string)
				)
			else:
				raise ValueError(f'missing required parameter "{n}"')

		macro_filename = join_norm(
			self.resolved_model_path,
			'Database','macros','call','skim.transit.all'
		)
		_logger.debug(f"writing updates to: {macro_filename}")
		with open(macro_filename, 'wt') as f:
			f.write(y)

	def  _manipulate_transit_assignment (self, params):
		# There are 4 values in this file that need to be edited.
		tokens_in_file = [
			'__base__fare__cta__', # line 113 -116
			'__base__fare__pace__',
			'__base__fare__metra__',
			'__base__fare__trans__',
		]
		# It need to be revised into
		computed_params = params.copy()
		computed_params['__base__fare__cta__']   = int(np.round( 150 * params['transit_fares']))
		computed_params['__base__fare__pace__']  = int(np.round( 150 * params['transit_fares']))
		computed_params['__base__fare__metra__'] = int(np.round( 136 * params['transit_fares']))
		computed_params['__base__fare__trans__'] = int(np.round(-120 * params['transit_fares']))

		with open(template('assign_transit.v2.template'), 'rt') as f:
			y = f.read()

		for n in tokens_in_file:
			if n in computed_params:
				y = y.replace(
					f"__EMAT_PROVIDES{n}",
					str(computed_params[n])
				)
			else:
				raise ValueError(f'missing required parameter "{n}"')

		macro_filename = join_norm(
			self.resolved_model_path,
			'Database','transit_asmt_macros','assign_transit.v2.mac'
		)
		_logger.debug(f"writing updates to: {macro_filename}")
		with open(macro_filename, 'wt') as f:
			f.write(y)


	def run(self):
		"""
		Run the core model.

		This method is the place where the core model run takes place.
		Note that this method takes no arguments; all the input
		exogenous uncertainties and policy levers are delivered to the
		core model in the `setup` method, which will be executed prior
		to calling this method. This facilitates debugging, as the `setup`
		method can potentially be used without the `run` method, allowing
		the user to manually inspect the prepared files and ensure they
		are correct before actually running a potentially expensive model.
		When running experiments, this method is called once for each core
		model experiment, after the `setup` method completes.

		If the core model requires some post-processing by `post_process`
		method defined in this API, then when this function terminates
		the model directory should be in a state that is ready to run the
		`post_process` command next.

		Raises:
			UserWarning: If model is not properly setup
		"""
		_logger.info("CMAP EMAT Model RUN ...")

		cmd = 'EMAT_Submit_Full_Regional_Model.bat'

		_logger.debug(f"cmd = {cmd}")
		_logger.debug(f"exists = {os.path.exists(join_norm(self.resolved_model_path, 'Database', cmd))}")

		import subprocess

		# babysitter1_src = f"""
		# while ($true) {{
		# 	gci {join_norm(self.resolved_model_path, "Database")} -recurse -Include errors | ? {{ $_.length -gt 50mb }} | Clear-Content;
		# 	sleep 15;
		# }}
		# """
		# babysitter2_src = f"""
		# while ($true) {{
		# 	gci {join_norm(self.resolved_model_path, "Database")} -recurse -Include blog.txt | ? {{ $_.length -gt 50mb }} | Clear-Content;
		# 	sleep 15;
		# }}
		# """
		# sitter1_path = join_norm(self.resolved_model_path, "Database", 'sitter1.ps1')
		# sitter2_path = join_norm(self.resolved_model_path, "Database", 'sitter2.ps1')
		# with open(sitter1_path, 'wt') as bs1:
		# 	bs1.write(babysitter1_src)
		# with open(sitter2_path, 'wt') as bs2:
		# 	bs2.write(babysitter2_src)
		#
		# babysitter1 = subprocess.Popen(['Powershell.exe','-executionpolicy','remotesigned','-File',sitter1_path])
		# # Powershell.exe -executionpolicy remotesigned -File sitter1.ps
		# babysitter2 = subprocess.Popen(['Powershell.exe','-executionpolicy','remotesigned','-File',sitter2_path])
		#
		babysitter = None
		try:
			# The subprocess.run command runs a command line tool. The
			# name of the command line tool, plus all the command line arguments
			# for the tool, are given as a list of strings, not one string.
			# The `cwd` argument sets the current working directory from which the
			# command line tool is launched.  Setting `capture_output` to True
			# will capture both stdout and stderr from the command line tool, and
			# make these available in the result to facilitate debugging.
			with subprocess.Popen(
					cmd,
					cwd=join_norm(self.resolved_model_path, "Database"),
					shell=True,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
			) as process:

				babysitter_src = f"""
				while ($true) {{
					gci {join_norm(self.resolved_model_path, "Database")} -recurse -Include blog.txt | ? {{ $_.length -gt 10kb }} | TASKKILL /F /PID {process.pid} /T > killed.txt;
					sleep 15;
				}}
				"""
				babysitter_path = join_norm(self.resolved_model_path, "Database", 'babysitter.ps1')
				with open(babysitter_path, 'wt') as bs:
					bs.write(babysitter_src)
				babysitter = subprocess.Popen(
					['Powershell.exe', '-executionpolicy', 'remotesigned', '-File', babysitter_path]
				)

				try:
					stdout, stderr = process.communicate()
				except:  # Including KeyboardInterrupt, communicate handled that.
					process.kill()
					# We don't call process.wait() as .__exit__ does that for us.
					raise
				retcode = process.poll()
			self.last_run_result = subprocess.CompletedProcess(process.args, retcode, stdout, stderr)

			# To kill a subprocess and all children of that subprocess...
			#   from subprocess import Popen
			#   process = Popen(command, shell=True)
			#   Popen("TASKKILL /F /PID {pid} /T".format(pid=process.pid))

			if self.last_run_result.returncode:
				with open("_stdout.log", "a") as f:
					f.write("=======================\n")
					stdout = self.last_run_result.stdout
					if isinstance(stdout, bytes):
						stdout = stdout.decode()
					f.write(stdout)
				with open("_stderr.log", "a") as f:
					f.write("=======================\n")
					stderr = self.last_run_result.stderr
					if isinstance(stderr, bytes):
						stderr = stderr.decode()
					f.write(stderr)
				raise subprocess.CalledProcessError(
					self.last_run_result.returncode,
					self.last_run_result.args,
					self.last_run_result.stdout,
					self.last_run_result.stderr,
				)

		finally:
			if babysitter is not None:
				babysitter.terminate()

		_logger.info("CMAP EMAT Model RUN complete")

	def last_run_logs(self, output=None):
		"""
		Display the logs from the last run.
		"""
		if output is None:
			output = print
		def to_out(x):
			if isinstance(x, bytes):
				output(x.decode())
			else:
				output(x)
		try:
			last_run_result = self.last_run_result
		except AttributeError:
			output("no run stored")
		else:
			if last_run_result.stdout:
				output("=== STDOUT ===")
				to_out(last_run_result.stdout)
			if last_run_result.stderr:
				output("=== STDERR ===")
				to_out(last_run_result.stderr)
			output("=== END OF LOG ===")


	def post_process(self, params=None, measure_names=None, output_path=None):
		"""
		Runs post processors associated with particular performance measures.

		This method is the place to conduct automatic post-processing
		of core model run results, in particular any post-processing that
		is expensive or that will write new output files into the core model's
		output directory.  The core model run should already have
		been completed using `setup` and `run`.  If the relevant performance
		measures do not require any post-processing to create (i.e. they
		can all be read directly from output files created during the core
		model run itself) then this method does not need to be overloaded
		for a particular core model implementation.

		Args:
			params (dict):
				Dictionary of experiment variables, with keys as variable names
				and values as the experiment settings. Most post-processing
				scripts will not need to know the particular values of the
				inputs (exogenous uncertainties and policy levers), but this
				method receives the experiment input parameters as an argument
				in case one or more of these parameter values needs to be known
				in order to complete the post-processing.  In this demo, the
				params are not needed, and the argument is optional.
			measure_names (List[str]):
				List of measures to be processed.  Normally for the first pass
				of core model run experiments, post-processing will be completed
				for all performance measures.  However, it is possible to use
				this argument to give only a subset of performance measures to
				post-process, which may be desirable if the post-processing
				of some performance measures is expensive.  Additionally, this
				method may also be called on archived model results, allowing
				it to run to generate only a subset of (probably new) performance
				measures based on these archived runs. In this demo, the
				the argument is optional; if not given, all measures will be
				post-processed.
			output_path (str, optional):
				Path to model outputs.  If this is not given (typical for the
				initial run of core model experiments) then the local/default
				model directory is used.  This argument is provided primarily
				to facilitate post-processing archived model runs to make new
				performance measures (i.e. measures that were not in-scope when
				the core model was actually run).

		Raises:
			KeyError:
				If post process is not available for specified measure
		"""
		pass

	def archive(self, params, model_results_path=None, experiment_id=None):
		"""
		Copies model outputs to archive location.

		Args:
			params (dict):
				Dictionary of experiment variables
			model_results_path (str, optional):
				The archive path to use.  If not given, a default
				archive path is constructed based on the scope name
				and the experiment_id.
			experiment_id (int, optional):
				The id number for this experiment.  Ignored if the
				`model_results_path` argument is given.

		"""
		if model_results_path is None:
			if experiment_id is None:
				db = getattr(self, 'db', None)
				if db is not None:
					experiment_id = db.get_experiment_id(self.scope.name, None, params)
			model_results_path = self.get_experiment_archive_path(experiment_id)

		# We don't need to archive everything in the `Database` directory,
		# so we'll create a blank one inside the archive and then selectively
		# copy files into it.  Also we'll create blanks of other sub-folders we
		# will partially populate.
		os.makedirs(os.path.join(model_results_path, 'Database'), exist_ok=True)
		os.makedirs(os.path.join(model_results_path, 'Database', 'report'), exist_ok=True)

		warnlog = []

		#### SINGLE FILE ####

		def _singlefile(*pathargs):
			src = os.path.join(self.resolved_model_path, *pathargs)
			if os.path.exists(src):
				shutil.copy2(src, os.path.join(model_results_path, *pathargs))
			else:
				warnings.warn(f"ARCHIVE WARNING: File '{src}' not found")
				warnlog.append(f"ARCHIVE WARNING: File '{src}' not found")

		# Copy emat info
		_singlefile("_emat_parameters_.yml")
		_singlefile("_emat_experiment_id_.yml")

		# Copy emmebank, a single file inside the Database directory
		shutil.copy2(
			os.path.join(self.resolved_model_path, 'Database', "emmebank"),
			os.path.join(model_results_path, 'Database', "emmebank"),
		)

		# Copy final_run_statistics.rpt, a single file inside the Database\Report directory
		_singlefile('Database', 'report', "final_run_statistics.rpt")

		# Copy run_vmt_statistics.rpt, a single file inside the Database\Report directory
		_singlefile('Database', 'report', "run_vmt_statistics.rpt")

		# Copy run_vht_statistics.rpt, a single file inside the Database\Report directory
		_singlefile('Database', 'report', "run_vht_statistics.rpt")

		# Copy transit_report_100_work.txt, a single file inside the Database directory
		_singlefile('Database', "transit_report_100_work.txt")

		# Copy transit_report_100_nonwork.txt, a single file inside the Database directory
		_singlefile('Database', "transit_report_100_nonwork.txt")

		# Copy report_ej.txt, a single file inside the Database\Report directory
		_singlefile('Database', 'report', "report_ej.txt")

		# Copy interchange_times.txt, a single file inside the Database\Report directory
		_singlefile('Database', 'report', "interchange_times.txt")

		# Copy blog.txt, a single file inside the Database directory
		_singlefile('Database', "blog.txt")

		# Copy blog.txt, a single file inside the Database directory
		_singlefile('Database', "model_run_timestamp.txt")

		#### ENTIRE SUBDIRECTORY ####
		# Copy data, a directory inside the Database directory
		copy_tree(
			os.path.join(self.resolved_model_path, 'Database', "data"),
			os.path.join(model_results_path, 'Database', "data"),
		)

		# copy emx files by name explicitly
		# the destination location for these files
		dest = os.path.join(model_results_path, 'Database', "emmemat")
		# ensure the destination directory exists
		os.makedirs(dest, exist_ok=True)
		for filenum in [1,2,3,4,5,6,7,8,9,10,14,22,23,24,25,26,27,36,37,40,41,42,43,44,45,46,47,48,49,
						101,102,103,104,105,106,107,108,109,834,842,843,844,845,846,847,934]:
			src = os.path.join(self.resolved_model_path, 'Database', "emmemat", f"mf{filenum}.emx")
			if os.path.exists(src):
				shutil.copy2(src, dest)
			else:
				warnings.warn(f"ARCHIVE WARNING: File '{src}' not found")
				warnlog.append(f"ARCHIVE WARNING: File '{src}' not found")

		# The general criteria for the archive is to take everything that is
		# a last-iteration model output, but abandon things that are unmodified inputs
		# and intermediate or temporary files that EMME created along the way.
		# When in doubt, err on the side of archiving it.

		if warnlog:
			with open(os.path.join(model_results_path, 'emat_archive_warnings.txt'), 'at') as wf:
				for i in warnlog:
					wf.write(str(i))
					wf.write("\n")

		# # The file size of the models is epic, over 11GB per model copy.  Left unchecked
		# # this fills up the drive super-fast.  So, we need to clean up the model directory
		# # here to avoid crashes from out-of-memory errors.
		# try:
		# 	shutil.rmtree(self.resolved_model_path)
		# except:
		# 	_logger.exception("EXCEPTION IN MODEL DELETE")

def _tiered_file_parse(filename, sep):
	"""
	Parse a tiered mapping file.

	This file format is used for:
	- run_vmt_statistics.rpt
	- final_run_statistics.rpt

	Args:
		filename (str): Filename of the source .RPT file

	Returns:
		dict
	"""
	tier_marks = [None, ]
	tier_keys = [None, ]

	result = dict()

	markers = ['==', '--']

	with open(filename, 'rt') as file:
		lines = file.readlines()
		for line in lines:
			line = line.strip()
			if line[:2] in markers and line[:2] == line[-2:]:
				if line[:2] in tier_marks:
					while line[:2] != tier_marks[-1] and tier_marks[-1] is not None:
						# Bump back a level
						tier_marks = tier_marks[:-1]
						tier_keys = tier_keys[:-1]
				if line[:2] == tier_marks[-1]:
					# New key same tier
					key = line[2:-2].strip()
					tier_keys[-1] = key
				else:
					# New key next tier
					key = line[2:-2].strip()
					tier_marks.append(line[:2])
					tier_keys.append(key)

			if sep in line:
				key, val = line.split(sep)
				key = key.strip()
				val = val.strip()
				result[".".join([*tier_keys[1:], key])] = float(val)

	return result

def tiered_file_parse_colon(filename):
	"""
	Parse a tiered mapping file with colon separators.

	This file format is used for:
	- run_vmt_statistics.rpt
	- final_run_statistics.rpt

	Args:
		filename (str): Filename of the source .RPT file

	Returns:
		dict
	"""
	return _tiered_file_parse(filename, ":")

def tiered_file_parse_space(filename):
	"""
	Parse a tiered mapping file with space separators.

	This file format is used for:
	- report_ej.txt

	Args:
		filename (str): Filename of the source .txt file

	Returns:
		dict
	"""
	return _tiered_file_parse(filename, " ")


o_d_v_tag = re.compile(r"""^
\s*
([0-9]+)  # Origin
\s*
([0-9]+)\s*:\s*([0-9]+\.[0-9]+)  # Dest:Value
\s*
([0-9]+)\s*:\s*([0-9]+\.[0-9]+)  # Dest:Value
\s*
([0-9]+)\s*:\s*([0-9]+\.[0-9]+)  # Dest:Value
\s*
([0-9]+)\s*:\s*([0-9]+\.[0-9]+)  # Dest:Value
.*
$""", re.VERBOSE)

set_matrix = re.compile(r"^Matrix\s+(\S+)\s+(\S+).*$")


def interchange_file_parse(filename):
	"""
	Parse the interchange_times file.

	This file format is used only for:
	- interchange_times.txt

	Args:
		filename (str): Filename of the source .txt file

	Returns:
		dict
	"""
	current_matrix = ""

	result = dict()

	with open(filename, 'rt') as file:
		lines = file.readlines()
		for line in lines:
			line = line.strip()
			set_mat = set_matrix.search(line)
			if set_mat:
				current_matrix = f"{set_mat.group(1)}_{set_mat.group(2)}"
			o_d_v = o_d_v_tag.search(line)
			if o_d_v:
				result[f"{current_matrix}_{o_d_v.group(1)}_to_{o_d_v.group(2)}"] = float(o_d_v.group(3))
				result[f"{current_matrix}_{o_d_v.group(1)}_to_{o_d_v.group(4)}"] = float(o_d_v.group(5))
				result[f"{current_matrix}_{o_d_v.group(1)}_to_{o_d_v.group(6)}"] = float(o_d_v.group(7))
				result[f"{current_matrix}_{o_d_v.group(1)}_to_{o_d_v.group(8)}"] = float(o_d_v.group(9))

	return result


def double_tap_tiered_file_parse(filename, sep=':'):
	"""
	Parse a double-tiered mapping file.

	This file format is used for:
	- run_vht_statistics.rpt

	Args:
		filename (str): Filename of the source .RPT file

	Returns:
		dict
	"""
	key_prefix = None

	result = dict()

	markers = ['==', '--']

	with open(filename, 'rt') as file:
		lines = file.readlines()
		prev_line = ""
		for line in lines:
			line = line.strip()
			if line[:2] in markers and line[:2] == line[-2:]:
				if prev_line[:2] in markers and prev_line[:2] == prev_line[-2:]:
					key_prefix = prev_line[2:-2].strip()+"."+line[2:-2].strip()

			if sep in line and key_prefix:
				key, val = line.split(sep)
				key = key.strip()
				val = val.strip()
				result[".".join([key_prefix, key])] = float(val)

			prev_line = line


	return result
