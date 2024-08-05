# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway

from zeep import Client #, Settings
from base64 import b64decode
from oauthlib.common import urldecode
from Crypto.Cipher import AES
from datetime import datetime

class FSSettings(Document):
	supported_currencies = ["INR"]

	def	validate(self):
		create_payment_gateway("FS")
		call_hook_method("payment_gateway_enabled", gateway="FS")
		if not self.flags.ignore_mandatory:
			self.validate_fs_credentials()
	
	def	validate_fs_credentials(self):
		# login to SOAP Server
		login_res = self.fapi_login()
		self.login_status = login_res["Result"]
		if self.login_status == "OK":
			self.last_login_at = datetime.now()
		self.request_transfer_token()

	#@frappe.whitelist(allow_guest=True)
	def fapi_login(self):
		# Initialise the SOAP client
		global fs_client
		fs_client = Client("assets/payments/FS.wsdl")

		strPID = self.fs_user
		strPassword = self.get_password(fieldname="fs_password", raise_exception=False)

		login_res = fs_client.service.login(strPID, strPassword)
		return login_res

	def request_transfer_token(self):
		request = {"transferToken":""}
		token_res = fs_client.service.requestTransferToken(request)
		encrypted = urldecode(token_res)
		d = encrypted[0][0].split(";")
		key = "fstockencryptkey".encode("utf8")
		data = b64decode(d[0])
		iv = b64decode(d[1])
		
		decipher = AES.new(key, AES.MODE_CBC, iv)
		result = decipher.decrypt(data)

		transfer_token = result.decode("ascii").strip().strip('\x00')
		"""
		https://stackoverflow.com/questions/38883476/how-to-remove-those-x00-x00

		NUL chars are not treated as whitespace by default by strip(), and as such you need to specify explicitly.
		This can catch you out, as print() will of course not show the NUL chars. 
		My solution that I used was to clean the string using ".strip().strip('\x00')
		"""
		self.token_decrypted = transfer_token
		#with open('fapi_token5.txt', 'w') as file:
		#	file.write(str(transfer_token))
		#with open('fapi_token2.txt', 'w') as fp:
		#	fp.write('\n'.join('%s %s' % x for x in transfer_token))

		return transfer_token

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)


@frappe.whitelist(allow_guest=True)
def add_transfer_contribution(doc, method):

	def add_transfer(integration_request=None):
		fs_controller = frappe.get_doc("FS Settings")
		# Add a login timeout check here, if possible
		login_res = fs_controller.fapi_login()

		if login_res["Result"] == "OK":
			transfer_token = fs_controller.request_transfer_token()
			if (transfer_token):
				strAccountNumberFrom = frappe.get_value("Customer", doc.party, "custom_fs_account_number")
				strAccountNumberTo = doc.custom_fs_account_to
				fAmount = str(doc.paid_amount)
				check = "Yes"
				#with open('fapi_token8.txt', 'w') as file:
				#	file.write(str(strAccountNumberFrom, strAccountNumberTo, fAmount, strDescription, check, transfer_token))

				# Create an "Integration Request" in case of a fresh transfer
				if not integration_request:
					kwargs = {
						"Payment Entry ID": doc.name,
						"strAccountNumberFrom": strAccountNumberFrom,
						"strAccountNumberTo": strAccountNumberTo,
						"fAmount": fAmount,
						# String format example: EXTRA.CON/ACC-PAY-2024-00857/7OHL1V6ATI
						# string[0:5] extracts the first 4 chars of the string
						"strDescription": _("PTDC/{0}.CON/{1}").format((doc.custom_contribution_type)[0:5], (doc.name)[4:]),
						"check": check,
						"token": transfer_token
					}
					# Create integration log
					integration_request = create_request_log(kwargs, service_name="FS")
				# appending the integration_request name field as Transaction ID in strDescription
				strDescription = _("{0}/{1}").format(kwargs["strDescription"], integration_request.name)
				addTransfer_res = fs_client.service.addTransfer(
					strAccountNumberFrom, strAccountNumberTo, fAmount, strDescription, check, transfer_token)
				# Explore whether to store the default FS transaction message, or request for a transaction ID..
				doc.custom_remarks = 1
				doc.custom_fs_transfer_status = addTransfer_res["Result"]
				doc.remarks = addTransfer_res["Message"]
				if addTransfer_res["Result"] == "OK":
					integration_request.status = "Completed"
					integration_request.save(ignore_permissions=True)
					frappe.db.commit()
				else:
					integration_request.staus = "Failed"
					integration_request.save(ignore_permissions=True)
					frappe.db.commit()
					frappe.throw(addTransfer_res["Result"])
		else:
			frappe.throw(login_res["Result"])

	if doc.custom_contribution_type and doc.custom_fs_transfer_status and doc.custom_fs_transfer_status != "OK":
		# fetch the existing integration request with status "Queued" - for this "Payment Entry" doc
		integration_request_existing = frappe.get_all(
			"Integration Request",
			filters={"integration_request_service": "FS", "data['Payment Entry ID']": doc.name},
			fields=["name", "data"],
		)
		# Add logic to pass an existing "Integration Request", if any
		if integration_request_existing:
			integration_request = frappe.get_doc(integration_request_existing[0])
			add_transfer(integration_request)

	elif doc.custom_contribution_type and not doc.custom_fs_transfer_status:
		add_transfer()


@frappe.whitelist(allow_guest=True)
def add_transfer_billing():
	pass
