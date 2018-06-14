from ZTUtils import make_query

client_id, _ = context.ERP5Site_getFacebookClientIdAndSecretKey()

query = make_query({
   # Call at he context of the appropriate web_service.
   'client_id': client_id,
   'redirect_uri': "{0}/ERP5Site_callbackFacebookLogin".format(came_from or context.absolute_url()),
   'scope': 'email'
})

login_url = "https://www.facebook.com/v2.10/dialog/oauth"
if "?" not in login_url:
  login_url += "?"

return context.REQUEST.RESPONSE.redirect("{0}{1}".format(login_url, query))