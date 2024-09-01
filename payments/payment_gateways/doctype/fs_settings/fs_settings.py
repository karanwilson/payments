# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway
import json

from zeep import Client #, Settings
from base64 import b64decode
from oauthlib.common import urldecode
from Crypto.Cipher import AES
from datetime import datetime

class FSSettings(Document):
	supported_currencies = ["INR"]
	# Initialise the SOAP client
	fs_client = Client("assets/payments/FS.wsdl")

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
			self.login_at = datetime.now()
		#self.request_transfer_token()


	def fapi_login(self):
		strPID = self.fs_user
		strPassword = self.get_password(fieldname="fs_password", raise_exception=False)

		login_res = self.fs_client.service.login(strPID, strPassword)
		return login_res

	def fapi_logout(self):
		request = ""
		return self.fs_client.service.logout(request)


	def request_transfer_token(self):
		request = {"transferToken":""}
		token_res = self.fs_client.service.requestTransferToken(request)
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
def login():
	fs_controller = frappe.get_doc("FS Settings")
	login_res = fs_controller.fapi_login()
	return login_res["Result"]

@frappe.whitelist(allow_guest=True)
def logout():
	fs_controller = frappe.get_doc("FS Settings")
	return fs_controller.fapi_logout()


@frappe.whitelist(allow_guest=True)
def get_account_max_amount(fs_acc_customer):
	fs_account_number = frappe.get_value("Customer", fs_acc_customer, "custom_fs_account_number")

	if fs_account_number:
		fs_controller = frappe.get_doc("FS Settings")
		login_res = fs_controller.fapi_login()

		if login_res["Result"] == "OK":
			accountMaxAmount_res = fs_controller.fs_client.service.getAccountMaxAmount(fs_account_number)
			response = {
				"Result": accountMaxAmount_res["Result"],
				"maxAmount": accountMaxAmount_res["maxAmount"]
			}
			return response


@frappe.whitelist(allow_guest=True)
def add_transfer_contribution(doc, method):
	# check if the Payment Entry is for PTDC Contibutions - the function doesn't run in case of returns and other Payment Entries
	# the function also doesn't run in case a FS Transfer Status has value "OK"
	if doc.custom_contribution_type:

		fs_controller = frappe.get_doc("FS Settings")
		login_res = fs_controller.fapi_login()

		if login_res["Result"] == "OK":
			transfer_token = fs_controller.request_transfer_token()

			if transfer_token:
				strAccountNumberFrom = frappe.get_value(doc.party_type, doc.party, "custom_fs_account_number")

				payment_dict = {
					'reference_doctype': doc.party_type,
					'reference_docname': doc.party,
					"Payment Name": doc.doctype,
					"Payment ID": doc.name,
					"strAccountNumberFrom": strAccountNumberFrom,
					"strAccountNumberTo": fs_controller.fs_account,
					"fAmount": str(doc.paid_amount),
					# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
					# string[0:5] extracts the first 4 chars of the string
					"strDescription": _("PTDC/{0}.CON/{1}").format((doc.custom_contribution_type)[0:5], (doc.name)[4:]),
					"check": "Yes",
					"token": transfer_token
				}

				#with open('fapi.txt', 'w') as file:
				#	file.write(str())

				integration_request = None

				# if exists, fetch the existing integration request for this "Payment Entry" doc
				for integration_request_existing in frappe.get_all(
					"Integration Request",
					filters={"status": "Queued", "integration_request_service": "FS", },
					fields=["name", "data"],
				):
					data = json.loads(integration_request_existing.data)
					if data["Payment ID"] == doc.name :
						integration_request = frappe.get_doc("Integration Request", integration_request_existing)
						#payment_dict_json = frappe.as_json(payment_dict, indent=1)
						#frappe.db.set_value("Integration Request", integration_request_existing.name, "data", payment_dict_json)
						break

				# Create an "Integration Request" in case of a fresh transfer
				if not integration_request:
					# Create integration log
					integration_request = create_request_log(payment_dict, service_name="FS")

				# appending the integration_request name field as Transaction ID in strDescription
				payment_dict["strDescription"] = _("{0}/{1}").format(payment_dict["strDescription"], integration_request.name)

				addTransfer_res = fs_controller.fs_client.service.addTransfer(
					payment_dict["strAccountNumberFrom"],
					payment_dict["strAccountNumberTo"],
					payment_dict["fAmount"],
					payment_dict["strDescription"],
					payment_dict["check"],
					payment_dict["token"]
				)

				# Explore whether to store the default FS transaction message, or request for a transaction ID..
				doc.custom_remarks = 1
				doc.custom_fs_transfer_status = addTransfer_res["Result"]
				doc.remarks = addTransfer_res["Message"]

				if addTransfer_res["Result"] == "OK":
					integration_request.status = "Completed"
					integration_request.save(ignore_permissions=True)
					frappe.db.commit()
				else:
					integration_request.status = "Failed"
					integration_request.save(ignore_permissions=True)
					frappe.db.commit()
					frappe.throw(addTransfer_res["Result"])

			else:
				frappe.throw("FS transfer token not received")
		else:
			frappe.throw(login_res["Result"])


@frappe.whitelist(allow_guest=True)
def add_transfer_billing(invoice_doc, fAmount):
	invoice_dict = json.loads(invoice_doc)

	fs_controller = frappe.get_doc("FS Settings")
	login_res = fs_controller.fapi_login()

	if login_res["Result"] == "OK":
		transfer_token = fs_controller.request_transfer_token()

		if transfer_token:
			fAmount_float = float(fAmount) # converting to float in order to do check for negative amounts below
			if fAmount_float > 0:
				strAccountNumberFrom = frappe.get_value("Customer", invoice_dict["customer"], "custom_fs_account_number")
				strAccountNumberTo = fs_controller.fs_account

			else:
				# in case of returns, the amount will be a negative value,
				# hence convert it to postive, and swap the from/to FS account numbers, to make a return transfer
				fAmount = abs(fAmount_float)
				#frappe.throw(str(fAmount))
				strAccountNumberFrom = fs_controller.fs_account
				strAccountNumberTo = frappe.get_value("Customer", invoice_dict["customer"], "custom_fs_account_number")

			payment_dict = {
				'reference_doctype': "Customer",
				'reference_docname': invoice_dict["customer"],
				"Payment Name": invoice_dict["doctype"],
				"Payment ID": invoice_dict["name"],
				"strAccountNumberFrom": strAccountNumberFrom,
				"strAccountNumberTo": strAccountNumberTo,
				"fAmount": str(fAmount),
				# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
				# string[0:5] extracts the first 4 chars of the string
				"strDescription": _("PT-POS-Invoice/{0}").format(invoice_dict["name"]),
				"check": "Yes",
				"token": transfer_token
			}

			#with open('fapi.txt', 'w') as file:
			#	file.write(str())

			integration_request = None

			# if exists, fetch the existing integration request for this "Payment Entry" doc
			for integration_request_existing in frappe.get_all(
				"Integration Request",
				filters={"status": "Queued", "integration_request_service": "FS", },
				fields=["name", "data"],
			):
				data = json.loads(integration_request_existing.data)
				if data["Payment ID"] == invoice_dict["name"] :
					integration_request = frappe.get_doc("Integration Request", integration_request_existing)
					#payment_dict_json = frappe.as_json(payment_dict, indent=1)
					#frappe.db.set_value("Integration Request", integration_request_existing.name, "data", payment_dict_json)
					break

			# Create an "Integration Request" in case of a fresh transfer
			if not integration_request:
				# Create integration log
				integration_request = create_request_log(payment_dict, service_name="FS")

			# appending the integration_request name field as Transaction ID in strDescription
			payment_dict["strDescription"] = _("{0}/{1}").format(payment_dict["strDescription"], integration_request.name)

			addTransfer_res = fs_controller.fs_client.service.addTransfer(
				payment_dict["strAccountNumberFrom"],
				payment_dict["strAccountNumberTo"],
				payment_dict["fAmount"],
				payment_dict["strDescription"],
				payment_dict["check"],
				payment_dict["token"]
			)

			# Explore whether to store the default FS transaction message, or request for a transaction ID..
			response = {
				"custom_fs_transfer_status": addTransfer_res["Result"],
				"remarks": addTransfer_res["Message"]
			}

			if addTransfer_res["Result"] == "OK":
				integration_request.status = "Completed"
				integration_request.save(ignore_permissions=True)
				frappe.db.commit()
				return response

			else:
				integration_request.status = "Failed"
				integration_request.save(ignore_permissions=True)
				frappe.db.commit()
				return response

		else:
			frappe.throw("FS transfer token not received")

	else:
			frappe.throw(login_res["Result"])


@frappe.whitelist(allow_guest=True)
def add_transfer_draft_fs_bills():
	draft_fs_bills = frappe.get_all(
		"Sales Invoice",
		{
			"status": "Draft",
			"custom_fs_transfer_status": "PENDING"
		},
		pluck='name'
	)

	if draft_fs_bills:
		fs_controller = frappe.get_doc("FS Settings")

		for bill in draft_fs_bills:
			invoice_doc = frappe.get_doc("Sales Invoice", bill)

			login_res = fs_controller.fapi_login()
			if login_res["Result"] == "OK":
				transfer_token = fs_controller.request_transfer_token()

				if transfer_token:
					strAccountNumberFrom = frappe.get_value("Customer", invoice_doc.customer, "custom_fs_account_number")

					payment_dict = {
						'reference_doctype': "Customer",
						'reference_docname': invoice_doc.customer,
						"Payment Name": invoice_doc.doctype,
						"Payment ID": invoice_doc.name,
						"strAccountNumberFrom": strAccountNumberFrom,
						"strAccountNumberTo": fs_controller.fs_account,
						"fAmount": str(invoice_doc.total),
						# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
						# string[0:5] extracts the first 4 chars of the string
						"strDescription": _("PT-POS-Invoice/{0}").format(invoice_doc.name),
						"check": "Yes",
						"token": transfer_token
					}

					integration_request = None

					# if exists, fetch the existing integration request for this "Payment Entry" doc
					for integration_request_existing in frappe.get_all(
						"Integration Request",
						filters={"status": "Queued", "integration_request_service": "FS", },
						fields=["name", "data"],
					):
						data = json.loads(integration_request_existing.data)
						if data["Payment ID"] == invoice_doc.name :
							integration_request = frappe.get_doc("Integration Request", integration_request_existing)
							#payment_dict_json = frappe.as_json(payment_dict, indent=1)
							#frappe.db.set_value("Integration Request", integration_request_existing.name, "data", payment_dict_json)
							break

					# Create an "Integration Request" in case of a fresh transfer
					if not integration_request:
						# Create integration log
						integration_request = create_request_log(payment_dict, service_name="FS")

					# appending the integration_request name field as Transaction ID in strDescription
					payment_dict["strDescription"] = _("{0}/{1}").format(payment_dict["strDescription"], integration_request.name)

					addTransfer_res = fs_controller.fs_client.service.addTransfer(
						payment_dict["strAccountNumberFrom"],
						payment_dict["strAccountNumberTo"],
						payment_dict["fAmount"],
						payment_dict["strDescription"],
						payment_dict["check"],
						payment_dict["token"]
					)

					#response = {
					#	"custom_fs_transfer_status": addTransfer_res["Result"],
					#	"remarks": addTransfer_res["Message"]
					#}

					if addTransfer_res["Result"] == "OK":
						integration_request.status = "Completed"
						integration_request.save(ignore_permissions=True)
						frappe.db.commit()
						invoice_doc.custom_fs_transfer_status = addTransfer_res["Result"]
						invoice_doc.remarks = addTransfer_res["Message"]
						invoice_doc.save()
						invoice_doc.submit()
						#return response

					else:
						integration_request.status = "Failed"
						integration_request.save(ignore_permissions=True)
						frappe.db.commit()
						invoice_doc.custom_fs_transfer_status = addTransfer_res["Result"]
						invoice_doc.remarks = addTransfer_res["Message"]
						invoice_doc.save()
						frappe.throw(addTransfer_res["Result"])
						#return response

				else:
					frappe.throw("FS transfer token not received")

			else:
				frappe.throw(login_res["Result"])