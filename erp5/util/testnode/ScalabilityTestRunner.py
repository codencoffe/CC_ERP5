##############################################################################
#
# Copyright (c) 2011 Nexedi SA and Contributors. All Rights Reserved.
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsibility of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# guarantees and support are strongly advised to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
##############################################################################
import datetime
import os
import subprocess
import sys
import time
import glob
import SlapOSControler
import SlapOSMasterCommunicator
import json
import time
import shutil
import logging
import string
import random
import urlparse
import base64
import httplib
import Utils
import requests
import slapos.slap
import cPickle as pickle
from ProcessManager import SubprocessError, ProcessManager, CancellationError
from subprocess import CalledProcessError
from Updater import Updater
from erp5.util import taskdistribution
from erp5.util.benchmark.thread import TestThread
# for dummy slapos answer
import signal
from . import logger

FRONTEND_MASTER_DOMAIN = "scalability.nexedi.com"
# max time to instance changing state: 2 hour
MAX_INSTANCE_TIME = 60*60*2
# max time to generate frontend instance: 1.5 hour
MAX_FRONTEND_TIME = 60*90
# max time to register instance to slapOSMaster: 5 minutes
MAX_CREATION_INSTANCE_TIME = 60*10
# max time for a test: 20 minutes
MAX_TEST_CASE_TIME = 60*20
# max time to prepare SlapOS for testsuite (software installation, instances requests, etc.)
MAX_PREPARE_TEST_SUITE = 3600*10*1.0 # 10 hours
# max time for a test line creation: 5 minutes
MAX_CREATION_TEST_LINE = 60*10
# max time for bootstrapping an instance site
MAX_BOOTSRAPPING_TIME = 60*30
# max time to get a connection
MAX_CONNECTION_TIME = 60*5
# time to check site bootstrap
CHECK_BOOSTRAPPING_TIME = 60*2

REQUEST_URL_SCRIPT = "requestUrl"
SCALABILITY_TEST = "scalability_test"
TEST_SUITE_INIT = "__init__.py"

# access SR by password
TESTNODE_USER = "testnode"
HTACCESS = "/.htaccess"
HTPASSWD = "/.htpasswd"
PASSWORD_FILE = "/sr_pass"
PASSWORD_LENGTH = 10
HOSTFILE = "/hosts"
SR_DICT = "frontend_software_dict"
INSTANCE_DICT = "instances_dict"

class ScalabilityTestRunner():
  def __init__(self, testnode):
    self.testnode =  testnode
    self.slapos_controler = SlapOSControler.SlapOSControler(
                                  self.testnode.working_directory,
                                  self.testnode.config)
    # Create the slapos account configuration file and dir
    key = self.testnode.taskdistribution.getSlaposAccountKey()
    certificate = self.testnode.taskdistribution.getSlaposAccountCertificate()
    # Get Slapos Master Url
    self.slapos_url = ''
    try:
      self.slapos_url = self.testnode.taskdistribution.getSlaposUrl()
      if not self.slapos_url:
        self.slapos_url = self.testnode.config['server_url']
    except:
      self.slapos_url = self.testnode.config['server_url']
    
    # Get Slapos Master url used for api rest (using hateoas)
    self.slapos_api_rest_url = self.testnode.taskdistribution.getSlaposHateoasUrl()

    logger.info("SlapOS Master url is: %s", self.slapos_url)
    logger.info("SlapOS Master hateoas url is: %s", self.slapos_api_rest_url)
    
    self.key_path, self.cert_path, config_path = self.slapos_controler.createSlaposConfigurationFileAccount(
                                        key, certificate, self.slapos_url, self.testnode.config)
    self.slapos_communicator = None
    # Dict containing used to store which SR is not yet correctly installed.
    # looks like: {'comp_id1':'SR_urlA', 'comp_id2':'SR_urlA',..}
    self.remaining_software_installation_dict = {}
    
    # Protection to prevent installation of softwares after checking
    self.authorize_supply = True
    self.authorize_request = False
    # Used to simulate SlapOS answer (used as a queue)
    self.last_slapos_answer = []
    self.last_slapos_answer_request = []
    self.frontend_instance_guid = None
    
  def _prepareSlapOS(self, software_path, computer_guid, create_partition=0, state="available"):
    # create_partition is kept for compatibility
    """
    A proxy to supply : Install a software on a specific node
    """
    logger.info("testnode, supply : %s %s", software_path, computer_guid)
    if self.authorize_supply :
      self.remaining_software_installation_dict[computer_guid] = software_path
      self.slapos_communicator.supply(software_path, computer_guid, state)
      return {'status_code' : 0}                                          
    else:
      raise ValueError("Too late to supply now. ('self.authorize_supply' is False)")
      return {'status_code' : 1}     

  def _generateInstanceTitle(self, test_suite_title):
    """
    Generate an instance title using various parameter
    TODO : add some verification (to don't use unexisting variables)
    """
    instance_title = "Scalability-"
    instance_title += "("+test_suite_title+")-"
    instance_title += str(self.involved_nodes_computer_guid).replace("'","")
    instance_title += "-"+str(datetime.datetime.now().isoformat())+"-"
    instance_title += "timestamp="+str(time.time())
    return instance_title

  def _generateInstanceXML(self, software_configuration,
                           test_result, test_suite, frontend_software, frontend_instance_guid=None):
    """
    Generate a complete scalability instance XML configuration
    """
    config = software_configuration.copy()
    config.update({'scalability-launcher-computer-guid':self.launcher_nodes_computer_guid[0]})
    config.update({'scalability-launcher-title':self.testnode.config['test_node_title']})
    config.update({'test-result-path':test_result.test_result_path})
    config.update({'test-suite-revision':test_result.revision})
    config.update({'test-suite':test_suite})
    config.update({'test-suite-master-url':self.testnode.config['test_suite_master_url']})
    if frontend_instance_guid:
      config["frontend"] = {"software-url": frontend_software,
                            "virtualhostroot-http-port" : 8080,
                            "virtualhostroot-https-port" : 4443}
      config["sla-dict"]["instance_guid=%s" % frontend_instance_guid] = ["frontend"]
    return config

  def getDictionaryFromFile(self, dict_file):
    dictionary = {}
    if os.path.isfile(dict_file):
      with open(dict_file, 'r') as file:
        dictionary = pickle.loads(file.read())
    return dictionary

  def updateDictionaryFile(self, dict_file, dictionary, key, value):
    dictionary[key] = value
    with open(dict_file, 'w') as file:
      file.write(pickle.dumps(dictionary))

  def _createInstance(self, software_path, software_configuration,
                      test_result, test_suite, frontend_software, frontend_instance_guid=None):
    """
    Create scalability instance. If there is an old instance for this testsuite, it destroys it.
    """
    if self.authorize_request:
      slappart_directory = self.testnode.config['srv_directory'].rsplit("srv", 1)[0]
      instance_dict_file = slappart_directory + "var/" + INSTANCE_DICT
      instance_dict = self.getDictionaryFromFile(instance_dict_file)
      if test_suite in instance_dict:
        self.slapos_communicator.requestInstanceDestroy(instance_dict[test_suite])
      self.updateDictionaryFile(instance_dict_file, instance_dict, test_suite, self.instance_title)
      logger.info("testnode, request : %s", self.instance_title)
      config = self._generateInstanceXML(software_configuration,
                                         test_result, test_suite, frontend_software, frontend_instance_guid)
      request_kw = {"partition_parameter_kw": {"_" : json.dumps(config)} }
      self.slapos_communicator.requestInstanceStart(self.instance_title, request_kw)
      self.authorize_request = False
      return {'status_code' : 0}                                          
    else:
      raise ValueError("Softwares release not ready yet to launch instan\
ces or already launched.")
      return {'status_code' : 1}

  def prepareSlapOSForTestNode(self, test_node_slapos=None):
    """
    We will build slapos software needed by the testnode itself,
    """
    if self.testnode.taskdistribution.isMasterTestnode(
                           self.testnode.config['test_node_title']):
      pass
    return {'status_code' : 0} 

  # Dummy slapos answering
  def _getSignal(self, signal, frame):
    logger.debug("Dummy SlapOS Master answer received.")
    self.last_slapos_answer.append(True)
  def _prepareDummySlapOSAnswer(self):
    pid = os.getpid()
    logger.info("Dummy slapOS answer enabled, send signal to %s (kill -USR1 %s)"
                " to simulate a SlapOS (positive) answer.", pid, pid)
    signal.signal(signal.SIGUSR1, self._getSignal)
  def _comeBackFromDummySlapOS(self):
    logger.info("Dummy slapOS answer disabled, please don't send more signals.")
    # use SIG_USR (kill)
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
  def simulateSlapOSAnswer(self):
    if len(self.last_slapos_answer)==0:
      return False
    else:
      return self.last_slapos_answer.pop()
  # /Dummy slapos answering
    
  def isSoftwareReleaseReady(self, software_url, computer_guid):
    """
    Return true if the specified software on the specified node is installed.
    This method should communicates with SlapOS Master.
    """
    software_state = self.slapos_communicator._getSoftwareState(computer_guid)
    logger.info("Current software state: %s", software_state)
    return software_state == SlapOSMasterCommunicator.SOFTWARE_STATE_INSTALLED

  def remainSoftwareToInstall(self):
    """
    Return True if it remains softwares to install, otherwise return False
    """
    # Remove from grid installed software entries
    for computer_guid, software_path in self.remaining_software_installation_dict.items():
      if self.isSoftwareReleaseReady(software_path, computer_guid):
        del self.remaining_software_installation_dict[computer_guid]
    # Not empty grid means that all softwares are not installed
    return len(self.remaining_software_installation_dict) > 0

  def _updateInstanceXML(self, software_configuration, instance_title,
                         test_result, test_suite, frontend_software, frontend_instance_guid=None):
    logger.info("testnode, updateInstanceXML : %s", instance_title)
    config = self._generateInstanceXML(software_configuration,
                                       test_result, test_suite, frontend_software, frontend_instance_guid)
    request_kw = {"partition_parameter_kw": {"_" : json.dumps(config)} }
    self.slapos_communicator.requestInstanceStart(instance_title, request_kw)

    logger.info("Waiting for new configuration")
    time.sleep(60*5)
    self.slapos_communicator.requestInstanceStop(instance_title, request_kw)
    self.slapos_communicator.waitInstanceStopped(instance_title)
    self.slapos_communicator.requestInstanceStart(instance_title, request_kw)

    return {'status_code' : 0} 

  def _waitInstanceCreation(self, instance_title, max_time=MAX_CREATION_INSTANCE_TIME):
    """
    Wait for 'max_time' the instance creation
    """
    logger.debug("Waiting for instance creation...")
    start_time = time.time()
    while (not self.slapos_communicator.isInstanceRequested(instance_title) \
           and (max_time > (time.time()-start_time)) ):
      logger.debug("Instance not ready yet. Sleeping 5 sec.")
      time.sleep(5)
    if (time.time()-start_time) > max_time:
      raise ValueError("Instance '%s' not found after %s seconds" %(instance_title, max_time))
    logger.debug("Instance found on slapOSMaster")

  def _initializeSlapOSConnection(self):
    """
    Initialize communication with slapos 
    """
    slap = slapos.slap.slap()
    retry = 0
    while True:
      # wait until _hateoas_navigator is loaded.
      if retry > 100:
         break
      slap.initializeConnection(self.slapos_url, 
                                self.key_path, 
                                self.cert_path, 
                                timeout=120, 
                                slapgrid_rest_uri=self.slapos_api_rest_url)
      if getattr(slap, '_hateoas_navigator', None) is None:
         retry += 1
         logger.info("Fail to load _hateoas_navigator waiting a bit and retry.")
         time.sleep(30)
      else:
         break

    if getattr(slap, '_hateoas_navigator', None) is None:
      raise ValueError("Fail to load _hateoas_navigator")

    supply = slap.registerSupply()
    order = slap.registerOpenOrder()
    return slap, supply, order

  def generateProfilePasswordAccess(self):
    software_hash_directory = self.testnode.config['slapos_binary'].rsplit("bin/slapos", 1)[0]
    apache_htpasswd = software_hash_directory + "parts/apache/bin/htpasswd"
    testsuite_directory = self.testnode.config['repository_path_list'][0].rsplit('/', 1)[0]
    htaccess_file = open(testsuite_directory + HTACCESS, "w")
    file_content = """
AuthType Basic
AuthName "Password Protected Area"
AuthUserFile "%s%s"
Require valid-user
""" % (testsuite_directory, HTPASSWD)
    htaccess_file.write(file_content)
    htaccess_file.close()
    password_path = testsuite_directory + PASSWORD_FILE
    with open(password_path, "w") as password_file:
      password = ''.join(random.choice(string.digits + string.letters) for i in xrange(PASSWORD_LENGTH))
      password_file.write(password)
    user = TESTNODE_USER
    command = [apache_htpasswd, "-bc", testsuite_directory + HTPASSWD, user, password]
    self.testnode.process_manager.spawn(*command)
    return user, password

  def createSoftwareReachableProfilePath(self, node_test_suite):
    # Create an obfuscated link to the testsuite directory
    path_to_suite = os.path.join(
                    self.testnode.config['working_directory'],
                    node_test_suite.reference)
    self.obfuscated_link_path = os.path.join(
                    self.testnode.config['software_directory'],
                    self.randomized_path)
    if ( not os.path.lexists(self.obfuscated_link_path) and
         not os.path.exists(self.obfuscated_link_path) ) :
      try :
        os.symlink(path_to_suite, self.obfuscated_link_path)
        logger.info("testnode, Symbolic link (%s->%s) created.",
                    self.obfuscated_link_path, path_to_suite)
      except Exception:
        msg = "testnode, Unable to create symbolic link to the testsuite."
        logger.exception(msg)
        raise ValueError(msg)
    logger.info("Sym link : %s %s", path_to_suite, self.obfuscated_link_path)

    user, password = self.generateProfilePasswordAccess()
    logger.info("Software Profile password: %s" % password)
    # Construct the ipv6 obfuscated url of the software profile reachable from outside
    self.reachable_address = "http://%s:%s@%s" % (user, password,
      os.path.join("["+self.testnode.config['httpd_ip']+"]"+":"+self.testnode.config['httpd_software_access_port'],
      "software",self.randomized_path))
    self.reachable_profile = os.path.join(self.reachable_address, "software.cfg")

  def newFrontendMasterSoftware(self, frontend_master_reference, frontend_software):
    slappart_directory = self.testnode.config['srv_directory'].rsplit("srv", 1)[0]
    software_dict_file = slappart_directory + "var/" + SR_DICT
    software_dict = self.getDictionaryFromFile(software_dict_file)
    if frontend_master_reference in software_dict:
      if software_dict[frontend_master_reference] != frontend_software:
        self.updateDictionaryFile(software_dict_file, software_dict, frontend_master_reference, frontend_software)
        return True
    else:
      self.updateDictionaryFile(software_dict_file, software_dict, frontend_master_reference, frontend_software)
      return True
    return False

  def prepareFrontendMasterInstance(self, computer, frontend_software, test_suite_title):
    if not frontend_software:
      return None
    self.frontend_master_reference = "Scalability-Master-Frontend-"
    self.frontend_master_reference += "("+test_suite_title+")-"
    self.frontend_master_reference += str(computer).replace("'","")
    restart = True
    slap, supply, order = self._initializeSlapOSConnection()
    slapos_communicator = SlapOSMasterCommunicator.SlapOSTester(
				self.frontend_master_reference, slap, order, supply,
				frontend_software, computer_guid=computer)
    # Destroy old frontend instance if frontend software has changed for this testsuite
    if self.newFrontendMasterSoftware(self.frontend_master_reference, frontend_software):
      restart = False
      slapos_communicator.requestInstanceDestroy(self.frontend_master_reference)
    # Ask for software installation
    start_time = time.time()
    slapos_communicator.supply(frontend_software, computer)
    while (slapos_communicator._getSoftwareState() != SlapOSMasterCommunicator.SOFTWARE_STATE_INSTALLED
        and (MAX_PREPARE_TEST_SUITE > (time.time()-start_time))):
      logger.info("Waiting for frontend software installation")
      time.sleep(60)
    if slapos_communicator._getSoftwareState() != SlapOSMasterCommunicator.SOFTWARE_STATE_INSTALLED:
      raise ValueError("ERROR while installing frontend software")
    logger.info("Frontend software installed.")
    # Request master frontend instance
    master_request_kw = {"partition_parameter_kw": {"domain" : FRONTEND_MASTER_DOMAIN } }
    slapos_communicator.requestInstanceStart(self.frontend_master_reference, master_request_kw)
    slapos_communicator.waitInstanceStarted(self.frontend_master_reference)
    logger.info("Master Frontend instance started.")
    frontend_master_dict = slapos_communicator.getMasterFrontendDict()
    if not frontend_master_dict['frontend_master_ipv6']:
      raise ValueError("ERROR in master frontend: ipv6 url not found")
    if not frontend_master_dict['instance_guid']:
      raise ValueError("ERROR in master frontend: instance guid not found")
    parsed_url = urlparse.urlparse(frontend_master_dict['frontend_master_ipv6'])
    self.frontend_master_ipv6 = parsed_url.hostname
    if restart:
      slapos_communicator.requestInstanceStop(self.frontend_master_reference, master_request_kw)
      slapos_communicator.waitInstanceStopped(self.frontend_master_reference)
      slapos_communicator.requestInstanceStart(self.frontend_master_reference, master_request_kw)
      slapos_communicator.waitInstanceStarted(self.frontend_master_reference)
    return frontend_master_dict['instance_guid']

  def prepareSlapOSForTestSuite(self, node_test_suite):
    """
    Install testsuite softwares
    """
    logger.debug('Preparing SlapOS for Test Suite...')
    max_time = MAX_PREPARE_TEST_SUITE
    interval_time = 60
    start_time = time.time()

    # Initialize communication with slapos
    slap, supply, order = self._initializeSlapOSConnection()

    # Only master testnode must order software installation
    if self.testnode.taskdistribution.isMasterTestnode(self.testnode.config['test_node_title']):
      # Get from ERP5 Master the configuration of the cluster for the test
      test_configuration = Utils.deunicodeData(
          json.loads(self.testnode.taskdistribution.generateConfiguration(
                     node_test_suite.test_suite_title)))
      self.involved_nodes_computer_guid = test_configuration['involved_nodes_computer_guid']
      self.launchable = test_configuration['launchable']
      self.error_message = test_configuration['error_message']
      self.randomized_path = test_configuration['randomized_path']
      if not self.launchable:
        logger.info("Test suite %s is not actually launchable"
                    " with the current cluster configuration.", node_test_suite.test_suite_title)
        logger.info("ERP5 Master indicates : %s", self.error_message)
        return {'status_code' : 1}

      configuration_list = test_configuration['configuration_list']
      node_test_suite.edit(configuration_list=configuration_list)
      self.launcher_nodes_computer_guid = test_configuration['launcher_nodes_computer_guid']
      self.instance_title = self._generateInstanceTitle(node_test_suite.test_suite_title)
      self.frontend_software = configuration_list[0].get("frontend-url")

      self.createSoftwareReachableProfilePath(node_test_suite)
      logger.info("Software reachable profile path is: %s", self.reachable_profile)

      # Initialize SlapOS Master Communicator
      self.slapos_communicator = SlapOSMasterCommunicator.SlapOSTester(
					self.instance_title,
					slap, 
					order, 
					supply, 
					self.reachable_profile, 
					computer_guid=self.launcher_nodes_computer_guid[0])
      # Ask for SR installation
      for computer_guid in self.involved_nodes_computer_guid:
        self._prepareSlapOS(self.reachable_profile, computer_guid) 
      # From the line below we would not supply any more softwares
      self.authorize_supply = False
      # TODO : remove the line below wich simulate an answer from slapos master
      self._prepareDummySlapOSAnswer()
      # Waiting until all softwares are installed
      while (self.remainSoftwareToInstall() 
             and (max_time > (time.time()-start_time))):
        logger.info("Master testnode is waiting for the end of "
                    "all software installation (for %ss) PID=%s.",
                    int(time.time()-start_time), os.getpid())
        time.sleep(interval_time)
      # TODO : remove the line below wich simulate an answer from slapos master
      self._comeBackFromDummySlapOS()
      try:
        self.frontend_instance_guid = self.prepareFrontendMasterInstance(self.launcher_nodes_computer_guid[0],
                                                                         self.frontend_software,
                                                                         node_test_suite.test_suite_title)
      except Exception as e:
        logger.error("Error preparing frontend master instance: " + str(e))
        return {'status_code' : 1}
      if self.remainSoftwareToInstall() :
        # All softwares are not installed, however maxtime is elapsed, that's a failure.
        logger.error("All softwares are not installed.")
        return {'status_code' : 1}
      self.authorize_request = True
      logger.debug("All software installed.")
      # Launch instance
      try:
        self._createInstance(self.reachable_profile, 
                             configuration_list[0],
                             node_test_suite.test_result,
                             node_test_suite.test_suite,
                             self.frontend_software,
                             self.frontend_instance_guid)
        logger.debug("Scalability instance requested.")
        self._waitInstanceCreation(self.instance_title)
      except Exception as e:
        logger.error("Error creating instance: " + str(e))
        return {'status_code' : 1}

      return {'status_code' : 0}
    return {'status_code' : 1}

  def makeSuite(self, test_suite, location_list, **kwargs):
    import imp
    suite = None
    repo_location = None
    for location in location_list:
      try:
        module = imp.load_source(SCALABILITY_TEST, "%s/%s/%s" %(location, SCALABILITY_TEST, TEST_SUITE_INIT))
        suite_class = getattr(module, test_suite)
        suite = suite_class(**kwargs)
        repo_location = "%s/%s/" % (location, SCALABILITY_TEST)
      except:
        pass
    return suite, repo_location

  def getInstanceInformation(self, suite, count):
    logger.info("Getting instance information:")
    instance_information_time = time.time()
    instance_information = self.slapos_communicator.getInstanceUrlDict()
    while not instance_information['frontend-url-list'] and \
          time.time() - instance_information_time < MAX_FRONTEND_TIME:
      time.sleep(5*60)
      instance_information = self.slapos_communicator.getInstanceUrlDict()
    logger.info(instance_information)
    if not instance_information['frontend-url-list']:
      raise ValueError("Error getting instance information: frontend url not available")
    instance_url = suite.getScalabilityTestUrl(instance_information)
    bootstrap_url = suite.getBootstrapScalabilityTestUrl(instance_information, count)
    metric_url = suite.getScalabilityTestMetricUrl(instance_information)
    site_availability_url = suite.getSiteAvailabilityUrl(instance_information)
    return instance_url, bootstrap_url, metric_url, site_availability_url

  def bootstrapInstance(self, bootstrap_url):
    bootstrap_password = error_message = None
    if bootstrap_url:
      try:
          logger.info("Bootstrapping instance...")
          command = [self.userhosts_bin, self.requestUrlScript, bootstrap_url]
          result = self.testnode.process_manager.spawn(*command, **self.exec_env)
          if result['status_code']:
            error_message = "Error while bootstrapping: " + str(result['stdout'])
          else:
            response_dict = json.loads(result['stdout'])
            if response_dict["status_code"] != 0:
              error_message = "Error while bootstrapping: " + str(response_dict["error_message"])
            bootstrap_password = response_dict["password"]
          logger.info("Bootstrap password: " + str(bootstrap_password))
      except Exception as e:
        error_message = "Error while bootstrapping: " + str(e)
    return bootstrap_password, error_message

  def isInstanceReady(self, site_availability_url):
    error_message = None
    if not site_availability_url:
      return True, error_message
    start_time = time.time()
    while MAX_BOOTSRAPPING_TIME > time.time()-start_time:
      try:
        command = [self.userhosts_bin, self.requestUrlScript, site_availability_url]
        result = self.testnode.process_manager.spawn(*command, **self.exec_env)
        if result['status_code']:
          logger.info("Error checking site availability. Response: " + str(result['stdout']))
        if int(result['stdout']) == 0:
          logger.info("Site ready for test.")
          return True, error_message
        logger.info("Site is not available yet.")
        logger.info("[DEBUG] Site availability url response: %d" % int(result['stdout']))
        time.sleep(CHECK_BOOSTRAPPING_TIME)
      except Exception as e:
        logger.info("Error checking site availability: " + str(e))
        time.sleep(CHECK_BOOSTRAPPING_TIME)
    error_message = "Error while bootstrapping: max bootstrap time exceded."
    return False, error_message

  def prepareUserHosts(self, instance_url, software_bin_directory):
    parsed_url = urlparse.urlparse(instance_url)
    testsuite_directory = self.testnode.config['repository_path_list'][0].rsplit('/', 1)[0]
    host_file_path = testsuite_directory + HOSTFILE
    with open(host_file_path, "w") as hosts_file:
      file_content = """%s %s
""" % (self.frontend_master_ipv6, parsed_url.hostname)
      hosts_file.write(file_content)
    self.exec_env = os.environ.copy()
    self.exec_env.update({'HOSTS': host_file_path})
    self.exec_env.update({'raise_error_if_fail': False})
    software_hash_directory = self.testnode.config['slapos_binary'].rsplit("bin/slapos", 1)[0]
    self.userhosts_bin = software_hash_directory + "parts/userhosts/userhosts"
    self.requestUrlScript = software_bin_directory + REQUEST_URL_SCRIPT

  def runTestSuite(self, node_test_suite, portal_url):
    if not self.launchable:
      return {'status_code' : 1, 'error_message': "Current test_suite is not actually launchable." }
    configuration_list = node_test_suite.configuration_list
    test_list = range(0, len(configuration_list))
    try:
      test_result_proxy = self.testnode.taskdistribution.createTestResult(
                          node_test_suite.revision, test_list,
                          self.testnode.config['test_node_title'],
                          True, node_test_suite.test_suite_title,
                          node_test_suite.project_title)
    except Exception as e:
      return {'status_code' : 1, 'error_message': "Error in communication with master: " + str(e) }
    logger.debug("Test Result created.")
    count = 0
    error_message = None

    suite, repo_location = self.makeSuite(node_test_suite.test_suite, self.testnode.config['repository_path_list'])
    if suite == None:
      error_message = "Could not find test suite %s in the repositories" % node_test_suite.test_suite
      test_result_proxy.reportFailure(stdout=error_message)
      return {'status_code' : 1, 'error_message': error_message }

    # Each cluster configuration is tested
    for configuration in configuration_list:
      # First configuration doesn't need XML configuration update.
      if count > 0:
        logger.info("Requesting instance with configuration %d" % count)
        try:
          self._updateInstanceXML(configuration, self.instance_title,
                                  node_test_suite.test_result, node_test_suite.test_suite, self.frontend_software, self.frontend_instance_guid)
        except Exception as e:
          error_message = "Error updating instance configuration: " + str(e)
      try:
        self.slapos_communicator.waitInstanceStarted(self.instance_title)
      except Exception as e:
          error_message = "Error starting instance: " + str(e)
      if error_message:
        test_result_proxy.reportFailure(stdout=error_message)
        return {'status_code' : 1, 'error_message': error_message }
      logger.debug("INSTANCE CORRECTLY STARTED")

      # Start only the current test
      exclude_list=[x for x in test_list if x!=test_list[count]]
      try:
        test_result_line_proxy = test_result_proxy.start(exclude_list)
      except Exception as e:
        error_message = "Error in communication with master: " + str(e)
        break
      if test_result_line_proxy == None :
        error_message = "Test case already tested."
        break
      logger.info("Test case for count : %d is in a running state." % count)

      try:
        instance_url, bootstrap_url, metric_url, site_availability_url = self.getInstanceInformation(suite, count)
      except Exception as e:
        error_message = "Error getting testsuite information: " + str(e)
        break

      software_bin_directory = self.testnode.config['slapos_binary'].rsplit("slapos", 1)[0]
      try:
        self.prepareUserHosts(instance_url, software_bin_directory)
      except Exception as e:
        error_message = "Error preparing userhost: " + str(e)
        break

      bootstrap_password, error_message = self.bootstrapInstance(bootstrap_url)
      if error_message: break
      instance_ready, error_message = self.isInstanceReady(site_availability_url)
      if error_message: break

      slappart_directory = self.testnode.config['srv_directory'].rsplit("srv", 1)[0]
      log_path = slappart_directory + "var/log/"
      try:
        current_test = test_result_proxy.getRunningTestCase()
        current_test_data_json = json.loads(current_test)
        current_test_data = "%d,%s,%s" % (current_test_data_json["count"], current_test_data_json["title"], current_test_data_json["relative_path"])
      except Exception as e:
        error_message = "Error getting current test case information: " + str(e)
        break
      try:
        command = [ self.userhosts_bin ] + suite.getScalabilityRunCommand(software_bin_directory,
                                                   instance_url,
                                                   bootstrap_password,
                                                   node_test_suite.test_result.test_result_path,
                                                   node_test_suite.revision,
                                                   current_test_data,
                                                   self.testnode.config['test_node_title'],
                                                   self.testnode.config["test_suite_master_url"],
                                                   node_test_suite.test_suite,
                                                   repo_location,
                                                   log_path,
                                                   metric_url)
      except Exception as e:
        error_message = "Error getting current run scalability command from testsuite: " + str(e)
        break

      logger.info("Running test case...")
      test_thread = TestThread(self.testnode.process_manager, command, logger.info, env=self.exec_env)
      test_thread.start()

      # Wait for test case ending
      test_case_start_time = time.time()
      try:
        while test_result_line_proxy.isTestCaseAlive() and \
              test_result_proxy.isAlive() and \
              time.time() - test_case_start_time < MAX_TEST_CASE_TIME:
          time.sleep(60)
        if test_result_line_proxy.isTestCaseAlive():
          error_message = "Test case during for %s seconds, too long. (max: %s seconds). Test failure." \
                              %(str(time.time() - test_case_start_time), MAX_TEST_CASE_TIME)
          break
        logger.info("[DEBUG] Test case for current configuration finished")
        count += 1
        # Test cancelled, finished or in an undeterminate state.
        if not test_result_proxy.isAlive():
          logger.info("[DEBUG] Test case finished")
          if count == len(configuration_list):
            break
          # Cancelled or in an undeterminate state.
          error_message = "Test case cancelled or undeterminate state."
          break
      except Exception as e:
        error_message = "Error in communication with master: " + str(e)
        break

    # Remove installed software
    self.authorize_supply = True
    logger.info("Removing installed software")
    for computer_guid in self.involved_nodes_computer_guid:
      self._prepareSlapOS(self.reachable_profile, computer_guid, state="destroyed")

    # If error appears then that's a test failure.    
    if error_message:
      logger.info("There was an error during the testsuite run: " + str(error_message))
      try:
        test_result_line_proxy.stop(error_count=1, failure_count=1,
                                    stdout=error_message, stderr=error_message)
        test_result_proxy.reportFailure(stdout=error_message)
      except Exception as e:
        error_message = "Error in communication with master: " + str(e)
      logger.debug("Test Failed.")
      return {'status_code' : 1, 'error_message': error_message} 
    # Test is finished.
    logger.info("Test finished.")

    return {'status_code' : 0}

  def getRelativePathUsage(self):
    """
    Used by the method testnode.constructProfile() to know
    if the software.cfg have to use relative path or not.
    """
    return True
