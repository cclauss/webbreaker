#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = "Brandon Spruth (brandon.spruth2@target.com), Jim Nelson (jim.nelson2@target.com)," \
             "Matt Dunaj (matthew.dunaj@target.com)"
__copyright__ = "(C) 2017 Target Brands, Inc."
__contributors__ = ["Brandon Spruth", "Jim Nelson", "Matthew Dunaj", "Kyler Witting"]
__status__ = "Production"
__license__ = "MIT"

try:
    from signal import *
    from urlparse import urlparse
    import urllib
except ImportError:  # Python3
    import html.entities as htmlentitydefs
    from urllib.parse import urlparse
    import html.parser as HTMLParser
    import urllib.request as urllib

import requests.exceptions
from git.exc import GitCommandError
import click
from webbreaker import __version__ as version
from webbreaker.webbreakerlogger import Logger
from webbreaker.webinspectconfig import WebInspectConfig
from webbreaker.webinspectclient import WebinspectClient
from webbreaker.webinspectqueryclient import WebinspectQueryClient
from webbreaker.fortifyclient import FortifyClient
from webbreaker.fortifyconfig import FortifyConfig
from webbreaker.webinspectscanhelpers import scan_running
from webbreaker.webbreakerhelper import WebBreakerHelper
from webbreaker.gitclient import GitClient, write_agent_info, read_agent_info, format_git_url
from webbreaker.secretclient import SecretClient
from webbreaker.threadfixclient import ThreadFixClient
from webbreaker.threadfixconfig import ThreadFixConfig
from webbreaker.webinspectproxyclient import WebinspectProxyClient
import re
import sys
import subprocess

handle_scan_event = None
reporter = None


class Config(object):
    def __init__(self):
        self.debug = False


pass_config = click.make_pass_decorator(Config, ensure=True)


def fortify_prompt():
    fortify_user = click.prompt('Fortify user')
    fortify_password = click.prompt('Fortify password', hide_input=True)
    return fortify_user, fortify_password


def format_webinspect_server(server):
    server = server.replace('https://', '')
    server = server.replace('http://', '')
    return server


@click.group(help=WebBreakerHelper.help_description())
@pass_config
def cli(config):
    # Show something pretty to start
    webbreaker_ascii = WebBreakerHelper.ascii_motd()
    b = WebBreakerHelper.banner(text=webbreaker_ascii)

    sys.stdout.write(str("{0}\nVersion {1}\n".format(b, version)))
    sys.stdout.write(str("Logging to files: {}\n".format(Logger.app_logfile)))
    SecretClient().verify_secret()


@cli.group(help="""WebInspect is dynamic application security testing software for assessing security of Web
applications and Web services.""")
@pass_config
def webinspect(config):
    pass


@webinspect.command()
@click.option('--scan_name',
              type=str,
              required=False,
              help="Specify name of scan --scan_name ${BUILD_TAG}")
@click.option('--settings',
              type=str,
              default='Default',
              required=True,
              help="""Specify name of settings file, without the .xml extension. WebBreaker will 
                 by default try to locate this file in in the repo found in .config. If your 
                 file is not in the repo, you may instead pass an absolute path to the file""")
@click.option('--size',
              required=False,
              type=click.Choice(['medium', 'large']),
              help="Size of scanner required. Valid values if provided are 'medium' or 'large'")
@click.option('--scan_mode',
              required=False,
              type=click.Choice(['crawl', 'scan', 'all']),
              help="Overrides the setting scan mode value.  Acceptable values are crawl, scan, or all.")
@click.option('--scan_scope',
              required=False,
              help="Overrides the scope value.  Acceptable values are all, strict, children, and ancestors.")
@click.option('--login_macro',
              required=False,
              help="Overrides existing or adds a recorded login sequence to authenticate to the targeted application")
@click.option('--scan_policy',
              required=False,
              help="""Are either custom or built-in WebInspect policies, for example \n
                    AggressiveSQLInjection, AllChecks, ApacheStruts, Application,
                    Assault, CriticalsAndHighs, CrossSiteScripting, Development, Mobile, NoSQLAndNode.js
                    Assault, CriticalsAndHighs, CrossSiteScripting, Development, Mobile, NoSQLAndNode.js
                    OpenSSLHeartbleed, OWASPTop10ApplicationSecurityRisks2013, OWASPTop10ApplicationSecurityRisks2007
                    OWASPTop10ApplicationSecurityRisks2010, PassiveScan, Platform, PrivilegeEscalation,
                    QA, Quick, Safe, SOAP, SQLInjection, Standard and TransportLayerSecurity""")
@click.option('--scan_start',
              required=False,
              help="Type of scan to be performed list-driven or workflow-driven scan."
                   " Acceptable values are `url` or `macro`")
@click.option('--start_urls',
              required=False,
              multiple=True,
              help="""Enter a single url or multiple each with it's own --start_urls.\n
                    For example --start_urls http://test.example.com --start_urls http://test2.example.com""")
@click.option('--upload_settings',
              required=False,
              help="""--upload_settings, upload setting file to the webinspect host,
                    settings are hosted under webbreaker/etc/webinspect/settings,
                    all settings files end with an .xml extension, the xml extension is not needed
                    and shouldn't be included.""")
@click.option('--upload_policy',
              required=False,
              help="""--upload_policy xss, upload policy file to the webinspect scanner
                    policies are hosted under webbreaker/etc/webinspect/policies, all policy
                    files end with a .policy extension, the policy extension is not needed and
                    shouldn't be included.""")
@click.option('--upload_webmacros',
              required=False,
              help="""--upload_webmacro to the webinspect scanner macros are hosted under
                    webbreaker/etc/webinspect/webmacros, all webmacro files end with the .webmacro extension,
                     the extension should NOT be included.""")
@click.option('--fortify_user',
              required=False,
              help="--fortify_user authenticates the Fortify SSC user for uploading WebInspect `.fpr` formatted scan")
@click.option('--allowed_hosts',
              required=False,
              multiple=True,
              help="""Include the hosts to scan without the protocol or scheme http:// or https://,
                     either a single host or multiple hosts each with it's own --allowed_hosts.
                     If --allowed_hosts is not provided, all hosts explicitly stated within the option,
                     --start_urls will be used.  Keep in mind, if this option is used you must re-enter
                     your host as provided in --start_urls""")
@click.option('--workflow_macros',
              required=False,
              multiple=True,
              help="""--workflow_macros are located under webbreaker/etc/webinspect/webmacros.
                    Overrides the login macro. Acceptable values are login .webmacros files
                    available on the WebInspect scanner to be used.""")
@pass_config
def scan(config, **kwargs):
    # Setup our configuration...
    webinspect_config = WebInspectConfig()

    ops = kwargs.copy()
    # Convert multiple args from tuples to lists
    ops['start_urls'] = list(kwargs['start_urls'])
    ops['allowed_hosts'] = list(kwargs['allowed_hosts'])
    ops['workflow_macros'] = list(kwargs['workflow_macros'])

    # ...as well as pulling down webinspect server config files from github...
    try:
        webinspect_config.fetch_webinspect_configs(ops)
    except GitCommandError as e:
        Logger.console.critical("{} does not have permission to access the git repo, see log {}".format(
            webinspect_config.webinspect_git, Logger.app_logfile))
        Logger.app.critical("{} does not have permission to access the git repo: {}".format(
            webinspect_config.webinspect_git, e))
        sys.exit(1)

    # ...and settings...
    try:
        webinspect_settings = webinspect_config.parse_webinspect_options(ops)
    except AttributeError as e:
        Logger.app.error("Your configuration or settings are incorrect see log {}!!!".format(Logger.app_logfile))

    # OK, we're ready to actually do something now

    # The webinspect client is our point of interaction with the webinspect server farm
    try:
        webinspect_client = WebinspectClient(webinspect_settings)
    except (UnboundLocalError, EnvironmentError) as e:
        Logger.console.critical(
            "Incorrect WebInspect configurations found!! See log {}".format(str(Logger.app_logfile)))
        Logger.app.critical("Incorrect WebInspect configurations found!! {}".format(str(e)))
        exit(1)

    # if a scan policy has been specified, we need to make sure we can find/use it
    if webinspect_client.scan_policy:
        # two happy paths: either the provided policy refers to an existing builtin policy, or it refers to
        # a local policy we need to first upload and then use.

        if str(webinspect_client.scan_policy).lower() in [str(x[0]).lower() for x in webinspect_config.mapped_policies]:
            idx = [x for x, y in enumerate(webinspect_config.mapped_policies) if
                   y[0] == str(webinspect_client.scan_policy).lower()]
            policy_guid = webinspect_config.mapped_policies[idx[0]][1]
            Logger.app.info(
                "Provided scan_policy {} listed as builtin policyID {}".format(webinspect_client.scan_policy,
                                                                               policy_guid))
            Logger.app.info("Checking to make sure a policy with that ID exists in WebInspect.")
            if not webinspect_client.policy_exists(policy_guid):
                Logger.app.error(
                    "Scan policy {} cannot be located on the WebInspect server. Stopping".format(
                        webinspect_client.scan_policy))
                exit(1)
            else:
                Logger.app.info("Found policy {} in WebInspect.".format(policy_guid))
        else:
            # Not a builtin. Assume that caller wants the provided policy to be uploaded
            Logger.app.info("Provided scan policy is not built-in, so will assume it needs to be uploaded.")
            webinspect_client.upload_policy()
            policy = webinspect_client.get_policy_by_name(webinspect_client.scan_policy)
            if policy:
                policy_guid = policy['uniqueId']
            else:
                Logger.app.error("The policy name is either incorrect or it is not available in {}."
                                 .format('etc/webinspect/policies'))
                exit(1)

        # Change the provided policy name into the corresponding policy id for scan creation.
        policy_id = webinspect_client.get_policy_by_guid(policy_guid)['id']
        webinspect_client.scan_policy = policy_id
        Logger.app.debug("New scan policy has been set")

    # Upload whatever configurations have been provided...
    # All skipped unless explicitly declared in CLI
    if webinspect_client.webinspect_upload_settings:
        webinspect_client.upload_settings()

    if webinspect_client.webinspect_upload_webmacros:
        webinspect_client.upload_webmacros()

    # if there was a provided scan policy, we've already uploaded so don't bother doing it again. hack.
    if webinspect_client.webinspect_upload_policy and not webinspect_client.scan_policy:
        webinspect_client.upload_policy()

    Logger.app.info("Launching a scan")
    # ... And launch a scan.
    try:
        scan_id = webinspect_client.create_scan()
        if scan_id:
            Logger.app.debug("Starting scan handling")
            Logger.app.info("Execution is waiting on scan status change")
            with scan_running():
                webinspect_client.wait_for_scan_status_change(scan_id)  # execution waits here, blocking call
            status = webinspect_client.get_scan_status(scan_id)
            Logger.app.info("Scan status has changed to {0}.".format(status))

            if status.lower() != 'complete':  # case insensitive comparison is tricky. this should be good enough for now
                Logger.app.error('Scan is incomplete and is unrecoverable. WebBreaker will exit!!')
                handle_scan_event('scan_end')
                exit(1)
        else:
            exit(1)
        webinspect_client.export_scan_results(scan_id, 'fpr')
        webinspect_client.export_scan_results(scan_id, 'xml')

    except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
        Logger.console.error(
            "Unable to connect to WebInspect {0}, see log: {1}".format(webinspect_settings['webinspect_url'],
                                                                       Logger.app_logfile))
        Logger.app.error(
            "Unable to connect to WebInspect {0}, see also: {1}".format(webinspect_settings['webinspect_url'], e))

    # # TODO
    # # And wrap up by writing out the issues we found
    # # this should be moved into a function...probably a whole 'nother class, tbh
    # if scan_id:
    #     Logger.app.debug("Scan log: {}".format(webinspect_client.get_scan_log(scan_guid=scan_id)))
    #     with open('/tmp/' + webinspect_client.scan_name + '.issues', 'w') as outfile:
    #         end_date = str(datetime.datetime.now())
    #         sessions = json.loads(webinspect_client.get_scan_issues(scan_guid=scan_id))
    #         # inject scan-level data into each issue
    #         for session in sessions:
    #             print(24)
    #             issues = session['issues']
    #             print(25)
    #             for issue in issues:
    #                 print(26)
    #                 issue['scan_name'] = webinspect_settings['webinspect_settings']
    #                 issue['scan_policy'] = webinspect_settings['webinspect_overrides_scan_policy']
    #                 issue['end_date'] = end_date
    #                 output = str(json.dumps(issue))
    #                 outfile.write(output + '\n')

    Logger.app.info("Webbreaker WebInspect has completed.")


@webinspect.command('list')
@click.option('--server',
              required=False,
              multiple=True,
              help="Optional URL of webinspect server. If not provided, all servers will be "
                   "queried. Can be provided multiple times. "
                   "Ex) --server sample.webinspect.com:8083 --server sample.webinspect2.com:8083")
@click.option('--scan_name',
              required=False,
              help="Only list scans matching this scan_name")
@click.option('--protocol',
              required=False,
              type=click.Choice(['http', 'https']),
              default='https',
              help="The protocol used to contact the webinspect server. Default protocol is https")
@pass_config
def webinspect_list(config, server, scan_name, protocol):
    if len(server):
        servers = []
        for s in server:
            servers.append(format_webinspect_server(s))
    else:
        servers = [format_webinspect_server(e[0]) for e in WebInspectConfig().endpoints]

    for server in servers:
        query_client = WebinspectQueryClient(host=server, protocol=protocol)
        if scan_name:
            results = query_client.get_scan_by_name(scan_name)
            if results and len(results):
                print("Scans matching the name {} found on {}".format(scan_name, server))
                print("{0:80} {1:40} {2:10}".format('Scan Name', 'Scan ID', 'Scan Status'))
                print("{0:80} {1:40} {2:10}\n".format('-' * 80, '-' * 40, '-' * 10))
                for match in results:
                    print("{0:80} {1:40} {2:10}".format(match['Name'], match['ID'], match['Status']))
            else:
                Logger.app.error("No scans matching the name {} were found on {}".format(scan_name, server))

        else:
            results = query_client.list_scans()
            if results and len(results):
                print("Scans found on {}".format(server))
                print("{0:80} {1:40} {2:10}".format('Scan Name', 'Scan ID', 'Scan Status'))
                print("{0:80} {1:40} {2:10}\n".format('-' * 80, '-' * 40, '-' * 10))
                for scan in results:
                    print("{0:80} {1:40} {2:10}".format(scan['Name'], scan['ID'], scan['Status']))
            else:
                print("No scans found on {}".format(server))

        print('\n\n\n')


@webinspect.command('servers')
@pass_config
def servers_list(config):
    servers = [format_webinspect_server(e[0]) for e in WebInspectConfig().endpoints]
    print('\n\nFound WebInspect Servers')
    print('-' * 30)
    for server in servers:
        print(server)
    print('\n')


@webinspect.command()
@click.option('--server',
              required=True,
              help="URL of webinspect server. For example --server sample.webinspect.com:8083")
@click.option('--scan_name',
              required=True,
              help="Name of scan to be downloaded")
@click.option('--scan_id',
              required=False,
              help="ID of scan to be downloaded. Scan will be downloaded as [scan_name].[x]")
@click.option('-x',
              required=False,
              default="fpr",
              help="Desired file format of scan download. Extension is defaulted to .fpr")
@click.option('--protocol',
              required=False,
              type=click.Choice(['http', 'https']),
              default='https',
              help="The protocol used to contact the webinspect server. Default protocol is https")
@pass_config
def download(config, server, scan_name, scan_id, x, protocol):
    server = format_webinspect_server(server)
    query_client = WebinspectQueryClient(host=server, protocol=protocol)

    if not scan_id:
        results = query_client.get_scan_by_name(scan_name)
        if len(results) == 0:
            Logger.app.error("No scans matching the name {} where found on this host".format(scan_name))
        elif len(results) == 1:
            scan_id = results[0]['ID']
            Logger.app.info("Scan matching the name {} found.".format(scan_name))
            Logger.app.info("Downloading scan {}".format(scan_name))
            query_client.export_scan_results(scan_id, scan_name, x)
        else:
            Logger.app.info("Multiple scans matching the name {} found.".format(scan_name))
            print("{0:80} {1:40} {2:10}".format('Scan Name', 'Scan ID', 'Scan Status'))
            print("{0:80} {1:40} {2:10}\n".format('-' * 80, '-' * 40, '-' * 10))
            for result in results:
                print("{0:80} {1:40} {2:10}".format(result['Name'], result['ID'], result['Status']))
    else:
        if query_client.get_scan_status(scan_id):
            query_client.export_scan_results(scan_id, scan_name, x)
        else:
            Logger.console.error("Unable to find scan with ID matching {}".format(scan_id))


@webinspect.command(name='proxy',
                    short_help="Interact with WebInspect proxy",
                    help=WebBreakerHelper().webinspect_proxy_desc())
@click.option('--list',
              required=False,
              is_flag=True,
              help="List WebInspect proxies currently available")
@click.option('--port',
              required=False,
              help="Assign WebInspect proxy port")
@click.option('--proxy_id',
              required=False,
              help="Assign WebInspect proxy ID")
@click.option('--server',
              required=False,
              help="Optional URL of specific WebInspect server")
@click.option('--start',
              required=False,
              is_flag=True,
              help="Start a WebInspect proxy service")
@click.option('--stop',
              required=False,
              is_flag=True,
              help="Stop & delete a WebInspect proxy service")
def webinspect_proxy(list, port, proxy_id, server, start, stop):
    # TODO: setup config.ini
    proxy_client = WebinspectProxyClient(server, proxy_id, port)
    if list:
        results = proxy_client.list_proxy()
        if results and len(results):
            print("Proxies found on {}".format(proxy_client.host))
            print("{0:80} {1:40} {2:10}".format('Scan Name', 'Scan ID', 'Scan Status'))
            print("{0:80} {1:40} {2:10}\n".format('-' * 80, '-' * 40, '-' * 10))
            for match in results:
                print("{0:80} {1:40} {2:10}".format(match['instanceId'], match['address'], match['port']))
        else:
            Logger.app.error("No proxies found on {}".format(proxy_client.host))

    elif start:
        proxy_client.get_cert_proxy()
        result = proxy_client.start_proxy()
        if result:
            print("Proxy started on\t:\t{}".format(proxy_client.host))
            print("Instance ID\t\t:\t{}".format(result['instanceId']))
            print("Address\t\t\t:\t{}".format(result['address']))
            print("Port\t\t\t:\t{}".format(result['port']))
        else:
            Logger.app.error("Unable to start proxy on {}".format(proxy_client.host))

    elif stop:
        if proxy_id:
            proxy_client.delete_proxy()
        else:
            Logger.app.error("Please enter a proxy ID to delete.")

    else:
        Logger.app.error("Error: No proxy command was given.")
        return 1

@cli.group(help="""Collaborative web application for managing WebInspect and Fortify SCA security bugs
across the entire secure SDLC-from development to QA and through production.""")
@pass_config
def fortify(config):
    pass


@fortify.command('list')
@click.option('--fortify_user')
@click.option('--fortify_password')
@click.option('--application',
              required=False,
              help="Name of Fortify application which you would like to list versions of."
              )
@pass_config
def fortify_list(config, fortify_user, fortify_password, application):
    fortify_config = FortifyConfig()
    try:
        if fortify_user and fortify_password:
            Logger.app.info("Importing Fortify credentials")
            fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                           fortify_username=fortify_user,
                                           fortify_password=fortify_password)
            fortify_config.write_username(fortify_user)
            fortify_config.write_password(fortify_password)
            Logger.app.info("Fortify credentials stored")
        else:
            Logger.app.info("No Fortify username or password provided. Checking .config for credentials")
            if fortify_config.has_auth_creds():
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               fortify_username=fortify_config.username,
                                               fortify_password=fortify_config.password)
                Logger.app.info("Fortify username and password successfully found in .config")
            else:
                Logger.app.info("Fortify credentials not found in .config")
                fortify_user, fortify_password = fortify_prompt()
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               fortify_username=fortify_user,
                                               fortify_password=fortify_password)
                fortify_config.write_username(fortify_user)
                fortify_config.write_password(fortify_password)
                Logger.app.info("Fortify credentials stored")
        if application:
            fortify_client.list_application_versions(application)
        else:
            fortify_client.list_versions()
        Logger.app.info("Fortify list has successfully completed")
    except ValueError:
        Logger.app.error("Unable to obtain a Fortify API token. Invalid Credentials")
    except (AttributeError, UnboundLocalError) as e:
        Logger.app.critical("Unable to complete command 'fortify list': {}".format(e))


@fortify.command(name='download', help="Download the current fpr scan of an Fortify Version")
@click.option('--fortify_user')
@click.option('--fortify_password')
@click.option('--application',
              required=False,
              help="Name of the Fortify application that version belongs to. If this option is not provided, application_name from .config will be used.")
@click.option('--version',
              required=True,
              help="Name of Fortify application version which you would like to a scan of")
@pass_config
def fortify_download(config, fortify_user, fortify_password, application, version):
    fortify_config = FortifyConfig()
    if application:
        fortify_config.application_name = application
    try:
        if fortify_user and fortify_password:
            Logger.app.info("Importing Fortify credentials")
            fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                           project_template=fortify_config.project_template,
                                           application_name=fortify_config.application_name,
                                           fortify_username=fortify_user,
                                           fortify_password=fortify_password)
            fortify_config.write_username(fortify_user)
            fortify_config.write_password(fortify_password)
            Logger.app.info("Fortify credentials stored")
        else:
            Logger.app.info("No Fortify username or password provided. Checking .config for credentials")
            if fortify_config.has_auth_creds():
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               project_template=fortify_config.project_template,
                                               application_name=fortify_config.application_name,
                                               fortify_username=fortify_config.username,
                                               fortify_password=fortify_config.password)
                Logger.app.info("Fortify username and password successfully found in .config")
            else:
                Logger.app.info("Fortify credentials not found in .config")
                fortify_user, fortify_password = fortify_prompt()
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               project_template=fortify_config.project_template,
                                               application_name=fortify_config.application_name,
                                               fortify_username=fortify_user,
                                               fortify_password=fortify_password)
                fortify_config.write_username(fortify_user)
                fortify_config.write_password(fortify_password)
                Logger.app.info("Fortify credentials stored")
        version_id = fortify_client.find_version_id(application, version)
        if version_id:
            filename = fortify_client.download_scan(version_id)
            if filename:
                Logger.app.info("Scan file for version {} successfully written to {}".format(version_id, filename))
            else:
                Logger.app.error("Scan download for version {} has failed".format(version_id))
        else:
            Logger.app.error("No version matching {} found under {} in Fortify".format(version, application))
    except ValueError:
        Logger.app.error("Unable to obtain a Fortify API token. Invalid Credentials")
    except (AttributeError, UnboundLocalError) as e:
        Logger.app.critical("Unable to complete command 'fortify download': {}".format(e))


@fortify.command()
@click.option('--fortify_user')
@click.option('--fortify_password')
@click.option('--application',
              required=False,
              help="Name of the Fortify application that version belongs to. If this option is not provided, application_name from .config will be used.")
@click.option('--version',
              required=True,
              help="Name of Fortify application version which you would like to upload a scan to.")
@click.option('--scan_name',
              required=False,
              help="If the name of the file is different than --version, use this option to to specify the name of the file (without the extension)")
@pass_config
def upload(config, fortify_user, fortify_password, application, version, scan_name):
    fortify_config = FortifyConfig()
    # Fortify only accepts fpr scan files
    x = 'fpr'
    if application:
        fortify_config.application_name = application
    if not scan_name:
        scan_name = version
    try:
        if not fortify_user or not fortify_password:
            Logger.console.info("No Fortify username or password provided. Checking .config for secret")
            if fortify_config.has_auth_creds():
                Logger.console.info("Fortify credentials found in .config")
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               project_template=fortify_config.project_template,
                                               application_name=fortify_config.application_name, scan_name=version,
                                               extension=x, fortify_username=fortify_config.username,
                                               fortify_password=fortify_config.password)
            else:
                Logger.console.info("Fortify credentials not found in .config")
                fortify_user, fortify_password = fortify_prompt()
                fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                               project_template=fortify_config.project_template,
                                               application_name=fortify_config.application_name,
                                               fortify_username=fortify_user,
                                               fortify_password=fortify_password, scan_name=version,
                                               extension=x)
                fortify_config.write_username(fortify_user)
                fortify_config.write_password(fortify_password)
                Logger.console.info("Fortify credentials stored")
        else:
            fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                           project_template=fortify_config.project_template,
                                           application_name=fortify_config.application_name,
                                           fortify_username=fortify_user,
                                           fortify_password=fortify_password, scan_name=version, extension=x)
            fortify_config.write_username(fortify_user)
            fortify_config.write_password(fortify_password)
            Logger.console.info("Fortify credentials stored")

        reauth = fortify_client.upload_scan(file_name=scan_name)

        if reauth == -2:
            # The given application doesn't exist
            Logger.console.critical("Fortify Application {} does not exist. Unable to upload scan.".format(application))

    except:
        Logger.console.critical("Unable to complete command 'fortify upload'")


@fortify.command('scan')
@click.option('--fortify_user')
@click.option('--fortify_password')
@click.option('--application',
              required=False,
              help="Name of the Fortify application that version belongs to. If this option is not provided, application_name from fortify.ini will be used.")
@click.option('--version',
              required=True,
              help="Name of Fortify application version.")
@click.option('--build_id',
              required=True,
              help="Jenkins BuildID")
@pass_config
def fortify_scan(config, fortify_user, fortify_password, application, version, build_id):
    fortify_config = FortifyConfig()
    if application:
        fortify_config.application_name = application

    if not fortify_user or not fortify_password:
        Logger.console.info("No Fortify username or password provided. Checking fortify.ini for secret")
        if fortify_config.has_auth_creds():
            Logger.console.info("Fortify credentials found in fortify.ini")
            fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                           project_template=fortify_config.project_template,
                                           application_name=fortify_config.application_name, scan_name=version,
                                           fortify_username=fortify_config.username,
                                           fortify_password=fortify_config.password)
        else:
            Logger.console.info("Fortify credentials not found in fortify.ini")
            fortify_user, fortify_password = fortify_prompt()
            fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                           project_template=fortify_config.project_template,
                                           application_name=fortify_config.application_name,
                                           fortify_username=fortify_user,
                                           fortify_password=fortify_password, scan_name=version)
            fortify_config.write_username(fortify_user)
            fortify_config.write_password(fortify_password)
            Logger.console.info("Fortify credentials stored")

        pv_url = fortify_client.build_pv_url()

        if pv_url and pv_url != -1:
            write_agent_info('fortify_pv_url', pv_url)
            write_agent_info('fortify_build_id', build_id)
        else:
            Logger.console.critical("Unable to complete command 'fortify scan'")

    else:
        fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                       project_template=fortify_config.project_template,
                                       application_name=fortify_config.application_name,
                                       fortify_username=fortify_user,
                                       fortify_password=fortify_password, scan_name=version)
        fortify_config.write_username(fortify_user)
        fortify_config.write_password(fortify_password)
        Logger.console.info("Fortify credentials stored")
        pv_url = fortify_client.build_pv_url()
        if pv_url and pv_url != -1:
            write_agent_info('fortify_pv_url', pv_url)
            write_agent_info('fortify_build_id', build_id)
        else:
            Logger.console.critical("Unable to complete command 'fortify scan'")


@cli.group(help="""Administrative commands involving credentials and notifiers""")
@pass_config
def admin(config):
    pass


@admin.command()
@click.option('--email',
              is_flag=True,
              help="Optional flag which instructs WebBreaker to find contributors to notify via email")
@click.option('--git_url',
              required=True,
              help="The url of the Git repo from which to find contributors. Ex: --url https://github.com/target/webbreaker")
@pass_config
def notifier(config, email, git_url):
    try:
        if not email:
            Logger.console.info(
                "'webbreaker admin notifier' currently only supports email notifications. Please use the '--email' flag")
            return
        else:
            git_url = format_git_url(git_url)
            if not git_url:
                Logger.console.info("The git_url provided is invalid")
                return
            else:
                parser = urlparse(git_url)
                host = "{}://{}".format(parser.scheme, parser.netloc)
                path = parser.path
                r = re.search('\/(.*)\/', path)
                owner = r.group(1)
                r = re.search('\/.*\/(.*)', path)
                repo = r.group(1)
                git_client = GitClient(host=host)
                emails = git_client.get_all_emails(owner, repo)

                if emails:
                    write_agent_info('git_emails', emails)
                    write_agent_info('git_url', git_url)
                else:
                    Logger.console.info("Unable to complete command 'webbreaker admin notifier'")

    except (AttributeError, UnboundLocalError) as e:
        Logger.app.error("Unable to query git repo for email".format(e))


@admin.command()
@click.option('--start',
              required=False,
              is_flag=True,
              help="Optional flag which instruct WebBreaker to create an agent")
@pass_config
def agent(config, start):
    if not start:
        try:
            agent_data = read_agent_info()
            sys.stdout.write(str("Git URL: {}\n".format(agent_data['git_url'])))
            sys.stdout.write(str("Contributer Emails: {}\n".format(", ".join(agent_data['git_emails']))))
            sys.stdout.write(str("SSC URL: {}\n".format(agent_data['fortify_pv_url'])))
            sys.stdout.write(str("Build ID: {}\n".format(agent_data['fortify_build_id'])))
            # sys.stdout.write(str("Your agent.json file is complete...\n"))
        except TypeError as e:
            Logger.app.error("Unable to complete command 'admin': {}".format(e))
            sys.stdout.write(str("Unable to complete read agent configurations!\n"))
            return
    else:
        try:
            # If any data is missing, verifier will output and exit
            # verifier = AgentVerifier('webbreaker/etc/agent.json')
            pid = subprocess.Popen(['python', 'webbreaker/webbreakeragent/agent.py', 'webbreaker/etc/agent.json'])
            sys.stdout.write(str("WebBreaker agent started successfully.\n"))
        except TypeError as e:
            Logger.app.error("Unable to complete command 'admin agent': {}".format(e))
        return


@admin.command()
@pass_config
@click.option('--fortify',
              required=False,
              is_flag=True,
              help="Flag used to designate options as Fortify credentials")
@click.option('--webinspect',
              required=False,
              is_flag=True,
              help="Flag used to designate options as WebInspect credentials")
@click.option('--clear',
              required=False,
              is_flag=True,
              help="Clears stored credentials of either Fortify or WebInspect based on provided flag")
@click.option('--username',
              required=False)
@click.option('--password',
              required=False)
def credentials(config, fortify, webinspect, clear, username, password):
    if fortify:
        fortify_config = FortifyConfig()
        if clear:
            fortify_config.clear_credentials()
        else:
            if username and password:
                try:
                    # TODO: Remove Fortify Client - not used
                    fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                                   fortify_username=username, fortify_password=password)
                    fortify_config.write_username(username)
                    fortify_config.write_password(password)
                    sys.stdout.write(str("Credentials stored successfully\n"))
                except ValueError as e:
                    sys.stdout.write(str("Unable to validate Fortify credentials. Credentials were not stored\n"))

            else:
                username, password = fortify_prompt()
                try:
                    # TODO: Remove Fortify Client - not used
                    fortify_client = FortifyClient(fortify_url=fortify_config.ssc_url,
                                                   fortify_username=username, fortify_password=password)
                    fortify_config.write_username(username)
                    fortify_config.write_password(password)
                    sys.stdout.write(str("Credentials stored successfully\n"))
                except ValueError as e:
                    sys.stdout.write(str("Unable to validate Fortify credentials. Credentials were not stored\n"))
    elif webinspect:
        if clear:
            sys.stdout.write(str("There are currently no stored credentials for WebInspect\n"))
        else:
            sys.stdout.write(str("There are currently no stored credentials for WebInspect\n"))
    else:
        sys.stdout.write(str("Please specify either the --fortify or --webinspect flag\n"))


@admin.command(help="Generates a new encryption key and clears all stored credentials")
@pass_config
@click.option('-f', '--force',
              required=False,
              is_flag=True,
              help="Optional flag to prevent confirmation prompt")
def secret(config, force):
    secret_client = SecretClient()
    if secret_client.secret_exists():
        if not force:
            if click.confirm('All stored credentials will be deleted. Do you want to continue?'):
                secret_client.wipe_all_credentials()
                secret_client.write_secret(overwrite=True)
            else:
                sys.stdout.write(str("New secret was not written\n"))
        else:
            secret_client.wipe_all_credentials()
            secret_client.write_secret(overwrite=True)
    else:
        secret_client.write_secret()


@cli.group(help="Interaction with a ThreadFix API")
@pass_config
def threadfix(config):
    pass


@threadfix.command(help="List all teams (ID and Name) found on ThreadFix")
@pass_config
def teams(config):
    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    teams = threadfix_client.list_teams()
    if teams:
        print("{0:^10} {1:30}".format('ID', 'Name'))
        print("{0:10} {1:30}".format('-' * 10, '-' * 30))
        for team in teams:
            print("{0:^10} {1:30}".format(team['id'], team['name']))
        Logger.app.info("Successfully listed threadfix teams")
        print('\n\n')
    else:
        Logger.app.error("No teams were found")


@threadfix.command(help="List all applications for a given ThreadFix team")
@pass_config
@click.option('--team_id',
              required=False,
              help="ID of ThreadFix team you want to list applications of")
@click.option('--team',
              required=False,
              help="ThreadFix team application listing")
def applications(config, team_id, team):
    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    if not team_id and not team:
        Logger.app.error("Please specify either a team or team_id")
        return
    if team and not team_id:
        team_id = threadfix_client.get_team_id_by_name(team)
    if team_id is None:
        Logger.app.error("Unable to find team with name {}".format(team))
        return
    apps = threadfix_client.list_apps_by_team(team)
    if apps:
        print("{0:^10} {1:30}".format('ID', 'Name'))
        print("{0:10} {1:30}".format('-' * 10, '-' * 30))
        for app in apps:
            print("{0:^10} {1:30}".format(app['id'], app['name']))
        Logger.app.info("Successfully listed threadfix applications")
        print('\n\n')
    else:
        Logger.app.error("No applications were found for team_id {}".format(team_id))


@threadfix.command(help="Create a new application in ThreadFix")
@pass_config
@click.option('--team_id',
              required=False,
              help="ID of ThreadFix team this application will belong to")
@click.option('--team',
              required=False,
              help="Name of ThreadFix team this application will belong to")
@click.option('--application',
              required=True,
              help="Name of new application")
@click.option('--url',
              required=False,
              default=None,
              help="Option URL of new application")
def create(config, team_id, team, application, url):
    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    if not team_id and not team:
        Logger.app.error("Please specify either a team or team_id")
        return
    if team and not team_id:
        team_id = threadfix_client.get_team_id_by_name(team)
    if team_id is None:
        Logger.app.error("Unable to find team with application {}".format(team))
        return
    created_app = threadfix_client.create_application(team_id, application, url)
    if created_app:
        Logger.app.info("Application was successfully created with id {}".format(created_app['id']))
    else:
        Logger.app.error("Application was not created, either the application exists, invalid token, or ThreadFix"
                         " is unavailable!! ")


@threadfix.command(help="List all scans (ID, Scanner, and Filename) of a certain application in ThreadFix")
@pass_config
@click.option('--app_id',
              required=True,
              help="ID of ThreadFix Application to list scans of")
def scans(config, app_id):
    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    scans = threadfix_client.list_scans_by_app(app_id)
    if scans:
        print("{0:^10} {1:30} {2:30}".format('ID', 'Scanner Name', 'Filename'))
        print("{0:10} {1:30} {2:30}".format('-' * 10, '-' * 30, '-' * 30))
        for scan in scans:
            print("{0:^10} {1:30} {2:30}".format(scan['id'], scan['scannerName'], scan['filename']))
        Logger.app.info("Successfully listed threadfix scans")
        print('\n\n')
    else:
        Logger.app.error("No scans were found for app_id {}".format(app_id))


@threadfix.command(name='upload', help="Upload a local scan file to an application in ThreadFix")
@pass_config
@click.option('--app_id',
              required=False,
              help="ID of ThreadFix Application to upload this scan to")
@click.option('--application',
              required=False,
              help="Name of ThreadFix Application to upload this scan to")
@click.option('--scan_file',
              required=True,
              help="File to be upload. Ex) --scan_file my_scan.xml")
def threadfix_upload(config, app_id, application, scan_file):
    if not app_id and not application:
        Logger.app.error("Please specify either an application or app_id!")
        return

    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    if not app_id:
        Logger.app.info("Attempting to find application matching name {}".format(application))
        apps = threadfix_client.list_all_apps()
        if not apps:
            Logger.app.error("Failed to retrieve applications from ThreadFix")
            return
        else:
            matches = []
            for app in apps:
                if app['app_name'] == application:
                    matches.append(app.copy())
            if len(matches) == 0:
                Logger.app.error("No application was found matching name {}".format(application))
                return
            if len(matches) > 1:
                Logger.app.error(
                    "Multiple applications were found matching name {}. Please specify the desired ID from below.".format(
                        application))
                print("{0:^10} {1:55} {2:30}".format('App ID', 'Team', 'Application'))
                print("{0:10} {1:55} {2:30}".format('-' * 10, '-' * 55, '-' * 30))
                for app in matches:
                    print("{0:^10} {1:55} {2:30}".format(app['app_id'], app['team_name'], app['app_name']))
                print('\n\n')
                return
            else:
                app_id = matches[0]['app_id']

    upload_resp = threadfix_client.upload_scan(app_id, scan_file)
    if upload_resp:
        Logger.app.info("{}".format(upload_resp))
    else:
        Logger.app.error("Scan file failed to upload!")


@threadfix.command(name='list', help="List all applications across all teams")
@click.option('--team',
              required=False,
              default=None,
              help="Only list applications of teams matching a certain name")
@click.option('--application',
              required=False,
              default=None,
              help="Only list applications matching a certain name")
@pass_config
def threadfix_list(config, team, application):
    threadfix_config = ThreadFixConfig()
    threadfix_client = ThreadFixClient(host=threadfix_config.host, api_key=threadfix_config.api_key)
    applications = threadfix_client.list_all_apps(team, application)
    if applications is not False:
        if len(applications):
            print("{0:^10} {1:55} {2:30}".format('App ID', 'Team', 'Application'))
            print("{0:10} {1:55} {2:30}".format('-' * 10, '-' * 55, '-' * 30))
            for app in applications:
                print("{0:^10} {1:55} {2:30}".format(app['app_id'], app['team_name'], app['app_name']))
            print('\n\n')
            Logger.app.info("ThreadFix List successfully completed")
        else:
            query_info = ''
            if team is not None:
                query_info = ' with team name matching {}'.format(team)
            if application is not None:
                if query_info == '':
                    query_info = ' with application name matching {}'.format(application)
                else:
                    query_info = query_info + ' and application name matching {}'.format(application)
            Logger.app.info("No applications were found" + query_info)
    else:
        Logger.app.error("Possible cause could be your API token must be associated with a local account!!")


if __name__ == '__main__':
    cli()
