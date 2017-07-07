from bottle import Bottle, run, route, template, request, response, abort, static_file
from dns.resolver import dns
import requests
import json
import urllib
import re

# This is the IP address of the server I'm running this sample code. You can run the
# sample on localhost, but you'll need to edit your host file
_ip = '132.148.25.185'

# This is the name of the application where users configure their sites
_hosting_website = 'exampleservice.domainconnect.org'

# Domain Connect Provider and Service/Template
#
# This template takes two variables. The IP address for the A Record (IP=), and string put into a TXT record (RANDOMTEXT=)
#
# The template sets an A record and a TXT record into the zone. It was created as part of a hackathon and as such has this name
_provider = 'whdhackathon'
_template = 'whd-template-1'

# oAuth Client Name, Scope, and Secret. This is all specific to GoDaddy
oAuthConfig = {
    'GoDaddy' : {
        'client_id' : 'whdhackathon',
        'client_scope' : 'whd-template-1',
        'client_secret' : 'DomainConnectGeheimnisSecretString'
    }
}

_oauth_client_id = 'whdhackathon'
_oauth_client_scope = 'whd-template-1'
_oauth_client_secret = "DomainConnectGeheimnisSecretString"

# Handle the home page. This can be rendered for the service, or the individual sites
@route('/')
def index():

    # Get the host name
    host = request.headers['Host']

    # If the host is my hosting website, render the home page
    if host == _hosting_website:
        return template('index.tpl', {})
    else:
        # See if the text string was put into DNS
        messagetext = _get_messagetext(host)

        # We only render a site for domains that have been configured with a message
        if messagetext == None or messagetext == '':
            return abort(404)

        # Render the site 
        return template('site.tpl', {'host': request.headers['Host'], 'messagetext': messagetext})

# Handle the form post for setting up a new site
@route('/config', method='POST')
def config():

    host = request.headers['Host']

    # This only works for the hosting website
    if host != _hosting_website:
        return abort(404)

    # Get the domain/message and validate
    domain = request.forms.get('domain')
    message = request.forms.get('message')
    if domain == None or domain == '' or not _is_valid_hostname(domain) or message == None or message == '' or not _is_valid_message(message):
        return template('invalid_data.tpl')

    # See if the DNS Provider supports domain connect
    json_data = _get_domainconnect_json(domain)
    if json_data == None:
        return template('no_domain_connect.tpl')

    dns_provider = json_data['providerName']
    
    # See if our template is supported
    check_url = json_data['urlAPI'] + '/v2/domainTemplates/providers/' + _provider + '/services/' + _template     
    if not _check_template(check_url):
        return template('no_domain_connect.tpl')
    
    # Create the URL to oonfigure with domain connect synchronously
    synchronousTargetUrl = json_data['urlSyncUX'] + '/v2/domainTemplates/providers/' + _provider + '/services/' + _template + '/apply?domain=' + domain + '&RANDOMTEXT=shm:' + message + '&IP=' + _ip

    # Create the URL to configure domain connect asynchronously via oAuth
    
    asynchronousTargetUrl = None
    
    if oAuthConfig.has_key(dns_provider):
    
        # The redirect_url is part of oAuth and where the user will be sent after consent. Appended to this URL will be
        # the OAuth code
        redirect_url = "http://" + host + "/oauthresponse?domain=" + domain + "&message=" + message + "&urlAPI=" + json_data['urlAPI'] + "&dns_provider=" + dns_provider
        asynchronousTargetUrl = json_data['urlAsyncUX'] + '/v2/domainTemplates/providers/' + _provider + '/services/' + _template + '?' + \
            'domain=' + domain + \
            "&client_id=" + oAuthConfig[dns_provider]['client_id'] + \
            "&scope=" + oAuthConfig[dns_provider]['client_scope'] + \
            "&redirect_uri=" + urllib.quote(redirect_url)

    # Render the confirmation page
    return template('confirm.tpl', {'providerName' : json_data['providerName'], 'synchronousTargetUrl' : synchronousTargetUrl, 'asynchronousTargetUrl' : asynchronousTargetUrl})
    
# Handle the redirect back from the oAuth call
#
# Normally this would get the access token, store it, and then redirect to some other page on the client.
#
# Here we get the access token and simply return it to the client. The client will post it back to emulate 
# the async server call
@route("/oauthresponse")
def oauthresponse():

    host = request.headers['Host']

    # This only works for the hosting website
    if host != _hosting_website:
        return abort(404)

    # Get the data from the URL
    code = request.query.get("code")
    domain = request.query.get("domain")
    message = request.query.get("message")
    urlAPI = request.query.get('urlAPI')
    dns_provider = request.query.get('dns_provider')
    error = request.query.get('error')
    
    if error != None:
        return 'Error'
    
    else:
        # Take the oauth code and get an access token. This must be done fairly quickly as oauth codes have a short expiry
        url = urlAPI + "/v2/oauth/access_token?code=" + code + "&grant_type=authorization_code&client_id=" + _oauth_client_id + "&client_secret=" + oAuthConfig[dns_provider]['client_secret']
        
        # Some oauth implmentations ask for the original redirect url when getting the access token
        #redirect_url = "http://" + host + "/oauthresponse?domain=" + domain + "&message=" + message + "&urlAPI=" + urlAPI
        #"&redirect_uri=" + urllib.quote(redirect_url) 
        
        # Call the oauth provider and get the access token
        r = requests.post(url, verify=True)
        
        json_response = r.json()
        access_token = json_response['access_token']

        # Return a page. Normally you would store the access token and re-auth token and redirect the client browser
        return template('async.tpl', {"access_token" : access_token, "code": code, "json_response": json_response, "domain": domain, "message": message, "urlAPI" : urlAPI})

# Handle the form post for the processing the asynchronous setting using an oAuth access token. 
@route("/asyncconfig", method='POST')
def ascynconfig():
    host = request.headers['Host']

    # This only works for the hosting website
    if host != _hosting_website:
        return abort(404)

    # Get the domain name, message, acccess token
    domain = request.forms.get('domain')
    message = request.forms.get('message')
    access_token = request.forms.get('access_token')
    urlAPI = request.forms.get('urlAPI')

    # Might as well validate the form settings
    if domain == None or domain == '' or not _is_valid_hostname(domain) or message == None or message == '' or not _is_valid_message(message) or access_token == None or access_token == '' or urlAPI == None or urlAPI == '':
        return template('invalid_data.tpl')

    # This is the URL to call the api to apply the template
    url = urlAPI + '/v2/domainTemplates/providers/' + _provider + '/services/' + _template + '/apply?domain=' + domain + '&RANDOMTEXT=shm:' + message + '&IP=' + _ip

    # Call the api with the oauth acces bearer token
    r = requests.post(url, headers={'Authorization': 'Bearer ' + access_token}, verify=True)
    
    # If this fails, and there is a re-auth token, we could add this code here

    return 'Done'

# Runs the application for all hosts
def run_all():
    run(host='0.0.0.0', port=80, debug=True)

# Gets the message text put into DNS for a domain name
def _get_messagetext(domain):
    try:
        messagetext = None

        # Get the txt record for domain connect
        answers = dns.resolver.query(domain, 'TXT')
        for i in range(len(answers)):
            answer = answers[i].strings[0]
            
            if answer.startswith('shm:'):
                if messagetext == None:
                    messagetext = ''
                messagetext = messagetext + ' ' + answer[4:]

        return messagetext

    except:
        return None

def _check_template(url):
    try:
        r = requests.get(url, verify=True)
        if r.status_code == 200:
           return True
        return False
    except:
        return False
    
def _get_domainconnect_json(domain):

    try:
        # Get the txt record for domain connect from the domain
        answers = dns.resolver.query('_domainconnect.' + domain, 'TXT')
        if len(answers) != 1:
            return None

        # Get the value to do the json call
        host = answers[0].strings[0]

        # Form the URL to get the json and fetch it
        url = 'https://' + host + '/v2/' + domain + '/settings'
        r = requests.get(url, verify=True)
        return r.json()

    except:
        return None

# Valid host names pass simple domain name validation
def _is_valid_hostname(hostname):
    if len(hostname) < 4 or len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1] # strip exactly one dot from the right, if present
    allowed = re.compile("(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))

# A valid message can only contain certain characters
def _is_valid_message(message):
    if len(message) < 1 or len(message) > 255:
        return False
    return re.match("^[a-zA-Z ,.?!0-9]*$", message) is not None

@route('/static/<filename>')
def send_static(filename):
    return static_file(filename, root='static')
