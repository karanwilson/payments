# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
import frappe.model
from frappe.model.document import Document
from frappe.utils import call_hook_method, nowdate, get_last_day # for fetching the last date of the month
from frappe.integrations.utils import create_request_log
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from payments.utils import create_payment_gateway
import json

from zeep import Client
from zeep.transports import Transport
from base64 import b64decode
from oauthlib.common import urldecode
from Crypto.Cipher import AES
import datetime
from datetime import datetime

from phpserialize3 import *
#import os


class FSSettings(Document):
	supported_currencies = ["INR"]
	# Initialise the SOAP client
	transport = Transport(timeout=10, operation_timeout=15)
	fs_client = Client("assets/payments/FS.wsdl", transport=transport)
	production_service = fs_client.create_service("{urn:assets/payments/FS}FS_SoapBinding", "https://api3.avfs.org.in/server3.php")
	staging_service = fs_client.create_service("{urn:assets/payments/FS}FS_SoapBinding", "https://api3-staging.financialservice.org.in/server3.php")

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

		if self.production:
			return self.production_service.login(strPID, strPassword)
			#frappe.throw(str(login_res["ExtraInfo"]))
		else:
			return self.staging_service.login(strPID, strPassword)

	def fapi_logout(self):
		request = ""
		return self.fs_client.service.logout(request)


	def request_transfer_token(self):
		request = {"transferToken":""}
		if self.production:
			token_res = self.production_service.requestTransferToken(request)
		else:
			token_res = self.staging_service.requestTransferToken(request)

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
		#self.token_decrypted = transfer_token
		
		#token_int = int(transfer_token)
		#token_type = type(token_int)

		""" if os.path.exists('tax_cess.txt'):
			append_write = 'a' # append if already exists
		else:
			append_write = 'w' # make a new file if not
		with open('tax_cess.txt', append_write) as file:
			file.write() """

		#with open('fapi_token9.txt', 'w') as file:
		#	file.write(str(token_type))
		#with open('fapi_token2.txt', 'w') as fp:
		#	fp.write('\n'.join('%s %s' % x for x in transfer_token))
		#frappe.throw(str(token_int))

		return int(transfer_token)
		# As per the new FAPI SOAP call to requestTransferToken, the transfer_token is required to be an integer.


	def fetch_fs_accounts_data(self):
		request = {"accountRange":""}

		if self.production:
			accountsRange_res = self.production_service.getAccountRange(request)
		else:
			accountsRange_res = self.staging_service.getAccountRange(request)

		return accountsRange_res


	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)


def get_last_day_of_Month():
    today = nowdate()
    last_day_of_Month = get_last_day(today)
    return last_day_of_Month


@frappe.whitelist()
def login():
	fs_controller = frappe.get_doc("FS Settings")
	login_res = fs_controller.fapi_login()
	return login_res["Result"]

@frappe.whitelist()
def logout():
	fs_controller = frappe.get_doc("FS Settings")
	return fs_controller.fapi_logout()


@frappe.whitelist()
def fetch_fs_accounts_detail():
	fs_controller = frappe.get_doc("FS Settings")
	login_res = fs_controller.fapi_login()

	if login_res["Result"] == "OK":
		accountsRange = fs_controller.fetch_fs_accounts_data()

		if accountsRange["Result"] == 'OK':

			# deserializing the PHP response
			accounts_dict = loads(accountsRange["Accounts"])

			# storing in files for reference
			with open('loadFSaccounts.txt', 'w') as file:
				file.write(str(accountsRange))
			with open('loadFSaccounts.json', 'w') as file:
				file.write(str(accounts_dict))

			return {
				"Result": accountsRange["Result"],
				"RecordCount": accountsRange["RecordCount"],
				"Accounts": accounts_dict
			}
		
		else:
			frappe.msgprint(str(accountsRange))


@frappe.whitelist()
def get_account_max_amount(fs_acc_customer):
	fs_account_number = frappe.get_value("Customer", fs_acc_customer, "custom_fs_account_number")

	if fs_account_number:
		fs_controller = frappe.get_doc("FS Settings")
		login_res = fs_controller.fapi_login()

		if login_res["Result"] == "OK":
			if fs_controller.production:
				accountMaxAmount_res = fs_controller.production_service.getAccountMaxAmount(fs_account_number)
			else:
				accountMaxAmount_res = fs_controller.staging_service.getAccountMaxAmount(fs_account_number)
			response = {
				"Result": accountMaxAmount_res["Result"],
				"maxAmount": accountMaxAmount_res["maxAmount"],
			}
			return response

		else:
			return login_res["Result"]


@frappe.whitelist()
def add_transfer_contribution(doc, method):
	# check if the Payment Entry is for PTDC Contibutions - the function doesn't run in case of returns and other Payment Entries
	# the function also doesn't run in case a FS Transfer Status has value "OK"
	if doc.custom_contribution_type:

		integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": doc.name, "status": "completed"}, "name")
		if integration_request_existing:
			return

		fs_controller = frappe.get_doc("FS Settings")
		login_res = fs_controller.fapi_login()

		if login_res["Result"] == "OK":
			transfer_token = fs_controller.request_transfer_token()

			if transfer_token:
				strAccountNumberFrom = frappe.get_value(doc.party_type, doc.party, "custom_fs_account_number")

				payment_dict = {
					'reference_doctype': doc.doctype,
					'reference_docname': doc.name,
					"Customer Name": doc.party_type,
					"Customer ID": doc.party,
					#"Payment Name": "",
					#"Payment ID": ,
					"strAccountNumberFrom": strAccountNumberFrom,
					"strAccountNumberTo": fs_controller.fs_account,
					"fAmount": str(doc.paid_amount),
					# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
					# string[0:5] extracts the first 5 chars of the string
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

				if fs_controller.production:
					fs_service_proxy = fs_controller.production_service
				else:
					fs_service_proxy = fs_controller.staging_service

				addTransfer_res = fs_service_proxy.addTransfer(
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

	return


def refund_fs_payments(doc, method):
	if doc.mode_of_payment == "FS":
		integration_request = None

		integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": doc.name}, "name")
		if integration_request_existing:
			integration_request = frappe.get_doc("Integration Request", integration_request_existing)
			if integration_request.status == "Completed":
				frappe.msgprint("Integration Request Exists!")
				return

		fs_controller = frappe.get_doc("FS Settings")
		if fs_controller.production:
			fs_service_proxy = fs_controller.production_service
		else:
			fs_service_proxy = fs_controller.staging_service

		try:
			# FAPI stage-1
			login_res = fs_controller.fapi_login()
			if login_res["Result"] != "OK":
				frappe.msgprint(login_res["Result"])
				return

			# FAPI stage-2
			transfer_token = None
			transfer_token = fs_controller.request_transfer_token()
			if not transfer_token:
				frappe.msgprint("FS transfer token not received")
				return

			fAmount = doc.paid_amount

			strAccountNumberFrom = fs_controller.fs_account
			strAccountNumberTo = frappe.get_value("Customer", doc.party, "custom_fs_account_number")

			trans_date = nowdate()

			match doc.company:
				case "Pour Tous Canteen":
					strDescription = _("PTC/{0}/{1}").format(trans_date, doc.name)
				case "Pour Tous Purchasing Service":
					strDescription = _("PTPS/{0}/{1}").format(trans_date, doc.name)
				case "Auroville Bakery":
					strDescription = _("AVBK/{0}/{1}").format(trans_date, doc.name)
				case "AV Bakery Cafe":
					strDescription = _("AVBC/{0}/{1}").format(trans_date, doc.name)
				case "AV Bakery Cafe Townhall":
					strDescription = _("ABCT/{0}/{1}").format(trans_date, doc.name)
				case _:
					strDescription = _("{0}/{1}").format(trans_date, doc.name)

			payment_dict = {
				'reference_doctype': doc.doctype,
				'reference_docname': doc.name,
				"Customer Name": doc.party_name,
				"Customer ID": doc.party,
				"strAccountNumberFrom": strAccountNumberFrom,
				"strAccountNumberTo": strAccountNumberTo,
				"fAmount": str(fAmount),
				# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
				# string[0:5] extracts the first 5 chars of the string
				"strDescription": strDescription,
				"check": "Yes",
				"token": transfer_token
			}

			#with open('fapi.txt', 'w') as file:
			#	file.write(str())

			# Create integration log
			integration_request = create_request_log(payment_dict, service_name="FS")

			# appending the integration_request name field as Transaction ID in strDescription
			payment_dict["strDescription"] = _("{0}/{1}").format(strDescription, integration_request.name)

			# FAPI stage-3
			addTransfer_res = fs_service_proxy.addTransfer(
				payment_dict["strAccountNumberFrom"],
				payment_dict["strAccountNumberTo"],
				payment_dict["fAmount"],
				payment_dict["strDescription"],
				payment_dict["check"],
				payment_dict["token"]
			)

			if addTransfer_res["Result"] == "OK":
				payment_dict["fs_transfer_response"] = addTransfer_res["Message"]

				payment_dict_json = frappe.as_json(payment_dict, indent=1)
				integration_request.data = payment_dict_json

				integration_request.status = "Completed"
				integration_request.save(ignore_permissions=True)

			else:
				integration_request.status = "Failed"
				integration_request.save(ignore_permissions=True)

			frappe.db.commit()
			frappe.msgprint(addTransfer_res["Result"])
			return

		except Exception as err:
			if integration_request:
				integration_request.status = "Failed"
				integration_request.save(ignore_permissions=True)
				frappe.db.commit()

			raise err


@frappe.whitelist()
def add_transfer_billing(invoice_doc, fAmount, fs_acc_balance):
	invoice_dict = json.loads(invoice_doc)

	integration_request = None

	integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": invoice_dict["name"]}, "name")
	if integration_request_existing:
		integration_request = frappe.get_doc("Integration Request", integration_request_existing)
		if integration_request.status == "Completed":
			data = json.loads(integration_request.data)
			# appending the integration_request name field as Transaction ID in strDescription
			remarks = _("{0}/{1}").format(data["strDescription"], integration_request.name)
			return {
				"custom_fs_transfer_status": "OK",
				"remarks": remarks
			}
		else:
			return {
				"custom_fs_transfer_status": integration_request.status,
				"remarks": "Null"
			}

	fs_controller = frappe.get_doc("FS Settings")
	if fs_controller.production:
		fs_service_proxy = fs_controller.production_service
	else:
		fs_service_proxy = fs_controller.staging_service


	try:
		# FAPI stage-1
		login_res = fs_controller.fapi_login()
		if login_res["Result"] != "OK":
			return {
				"custom_fs_transfer_status": login_res["Result"],
				"remarks": "Null"
			}

		# FAPI stage-2
		transfer_token = None
		transfer_token = fs_controller.request_transfer_token()
		if not transfer_token:
			return {
				"custom_fs_transfer_status": "FS transfer token not received",
				"remarks": "Null"
			}

		fAmount_float = float(fAmount) # converting to float in order to do check for negative amounts below
		if fAmount_float > 0:
			if fAmount_float > float(fs_acc_balance):
				return {
					"custom_fs_transfer_status": "Insufficient Funds",
					"remarks": "Null"
				}

			strAccountNumberFrom = invoice_dict["custom_fs_account_number"]
			strAccountNumberTo = fs_controller.fs_account

		else:
			# in case of returns, the amount will be a negative value,
			# hence convert it to postive, and swap the from/to FS account numbers, to make a return transfer
			fAmount = abs(fAmount_float)
			#frappe.throw(str(fAmount))
			strAccountNumberFrom = fs_controller.fs_account
			strAccountNumberTo = invoice_dict["custom_fs_account_number"]

		if "custom_transaction_date" in invoice_dict:
			trans_date = invoice_dict["custom_transaction_date"]
		else:
			trans_date = invoice_dict["posting_date"]

		match invoice_dict["company"]:
			case "Pour Tous Canteen":
				strDescription = _("PTC/{0}/{1}").format(trans_date, invoice_dict["name"])
			case "Pour Tous Purchasing Service":
				strDescription = _("PTPS/{0}/{1}").format(trans_date, invoice_dict["name"])
			case "Auroville Bakery":
				strDescription = _("AVBK/{0}/{1}").format(trans_date, invoice_dict["name"])
			case "AV Bakery Cafe":
				strDescription = _("AVBC/{0}/{1}").format(trans_date, invoice_dict["name"])
			case "AV Bakery Cafe Townhall":
				strDescription = _("ABCT/{0}/{1}").format(trans_date, invoice_dict["name"])
			case _:
				strDescription = _("{0}/{1}").format(trans_date, invoice_dict["name"])

		payment_dict = {
			'reference_doctype': invoice_dict["doctype"],
			'reference_docname': invoice_dict["name"],
			"Customer Name": invoice_dict["customer_name"],
			"Customer ID": invoice_dict["customer"],
			#"Payment Name": "",
			#"Payment ID": ,
			"strAccountNumberFrom": strAccountNumberFrom,
			"strAccountNumberTo": strAccountNumberTo,
			"fAmount": str(fAmount),
			# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
			# string[0:5] extracts the first 5 chars of the string
			"strDescription": strDescription,
			"check": "Yes",
			"token": transfer_token
		}

		#with open('fapi.txt', 'w') as file:
		#	file.write(str())

		""" integration_request = None

		# if exists, fetch the existing integration request for this doc
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
		if not integration_request: """

		# Create integration log
		integration_request = create_request_log(payment_dict, service_name="FS")

		# appending the integration_request name field as Transaction ID in strDescription
		payment_dict["strDescription"] = _("{0}/{1}").format(strDescription, integration_request.name)

		# FAPI stage-3
		addTransfer_res = fs_service_proxy.addTransfer(
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

	except Exception as err:
		if integration_request:
			integration_request.status = "Failed"
			integration_request.save(ignore_permissions=True)
			frappe.db.commit()

		raise err
		""" return {
			"custom_fs_transfer_status": err,
			"remarks": "Null"
		} """


@frappe.whitelist()
def fetch_unpaid_sales_orders():
	today = nowdate()

	if frappe.defaults.get_user_default("company") == "Auroville Bakery":
		return frappe.db.sql(
			"""
			SELECT name FROM `tabSales Order`
			WHERE
				docstatus = 1
				AND ifnull(status, "") != "Closed"
				AND grand_total > advance_paid
				AND abs(100 - per_billed) > 0.01
				AND delivery_date <= '{0}'
			ORDER BY
				transaction_date, name
			""".format(today),
			#as_dict=1,
			#AND custom_fs_account_number IS NOT NULL
		)

	else:
		return frappe.db.sql(
			"""
			SELECT name FROM `tabSales Order`
			WHERE
				docstatus = 1
				AND ifnull(status, "") != "Closed"
				AND grand_total > advance_paid
				AND abs(100 - per_billed) > 0.01
				AND delivery_date <= '{0}'
			ORDER BY
				transaction_date, name
			""".format(today),
			#as_dict=1,
			#AND custom_fs_account_number IS NOT NULL
		)

@frappe.whitelist()
def add_transfer_sales_order(order):
	order_doc = frappe.get_doc("Sales Order", order)

	if order_doc.custom_is_donation:
		bank_account = get_bank_cash_account("Donations", order_doc.company)

		pe = get_payment_entry(
			dt = order_doc.doctype,
			dn = order_doc.name,
			bank_account = bank_account["account"],
		)

		pe.mode_of_payment = "Donations"

		if order_doc.custom_remarks:
			pe.reference_no = order_doc.custom_remarks
		else:
			pe.reference_no = "Donation"
		#pe.reference_date = nowdate()
		#pe.paid_amount = pe.received_amount = fAmount
		#pe.custom_fs_transfer_status = addTransfer_res["Result"]
		pe.cost_center = "Main - AB"
		pe.custom_remarks = 1
		pe.remarks = order_doc.custom_remarks

		pe.insert(ignore_permissions=True)
		pe.submit()
		frappe.db.commit()
		return { "OK" }

	#cust_fs_acc_number = frappe.get_value("Customer", order_doc.customer, "custom_fs_account_number")
	if not order_doc.custom_fs_account_number:
		#frappe.throw(str(order_doc.customer))
		customer_group = frappe.get_value("Customer", order_doc.customer, "customer_group")

		if customer_group == "Aurocard Payments":
			bank_account = get_bank_cash_account("Aurocard", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "Aurocard"
			if order_doc.custom_remarks:
				pe.reference_no = order_doc.custom_remarks
			else:
				pe.reference_no = "Not recorded, please check the bank/FS statements"
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			#pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = order_doc.custom_remarks

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()
			return { "OK" }

		elif customer_group == "UPI Payments":
			bank_account = get_bank_cash_account("UPI", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "UPI"
			if order_doc.custom_remarks:
				pe.reference_no = order_doc.custom_remarks
			else:
				pe.reference_no = "Not recorded, please check the bank/FS statements"
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			#pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = order_doc.custom_remarks

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()
			return { "OK" }

		elif customer_group == "Card Payments":
			bank_account = get_bank_cash_account("Cards", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "Cards"
			if order_doc.custom_remarks:
				pe.reference_no = order_doc.custom_remarks
			else:
				pe.reference_no = "Not recorded, please check the bank/FS statements"
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			#pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = order_doc.custom_remarks

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()
			return { "OK" }

		elif customer_group == "Cash Payments":
			bank_account = get_bank_cash_account("Cash", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "Cash"
			if order_doc.custom_remarks:
				pe.reference_no = order_doc.custom_remarks
			else:
				pe.reference_no = "Not recorded, please check the bank/FS statements"
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			#pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = order_doc.custom_remarks

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()
			return { "OK" }

		elif customer_group == "NEFT Payments":
			bank_account = get_bank_cash_account("NEFT", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "NEFT"
			if order_doc.custom_remarks:
				pe.reference_no = order_doc.custom_remarks
			else:
				pe.reference_no = "Not recorded, please check the bank/FS statements"
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			#pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = order_doc.custom_remarks

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()
			return { "OK" }


	integration_request = None # initialising before the try except statement, as it is referenced in the except clause

	# if exists, fetch the existing integration request
	integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": order_doc.name}, "name")
	if integration_request_existing:
		int_req_doc = frappe.get_doc("Integration Request", integration_request_existing)
		if int_req_doc.status == 'Completed':
			frappe.msgprint(
				msg=_("Duplicate Payment Request: Invoice {0} was paid with Integration Request {1}").format(order_doc.name, integration_request_existing),
				title='Error',
			)
			return

		else:
			integration_request = int_req_doc
			""" order_doc.custom_fs_transfer_status = int_req_doc.status
			order_doc.save()
			frappe.db.commit() """


	fs_controller = frappe.get_doc("FS Settings")

	try:
		# FAPI stage-1
		login_res = fs_controller.fapi_login()
		if login_res["Result"] != "OK":
			return

		if fs_controller.production:
			fs_service_proxy = fs_controller.production_service
		else:
			fs_service_proxy = fs_controller.staging_service

		fAmount = order_doc.grand_total - order_doc.advance_paid

		# FAPI stage-2
		accountMaxAmount_res = fs_service_proxy.getAccountMaxAmount(order_doc.custom_fs_account_number)
		if accountMaxAmount_res["Result"] == "OK":
			fAmount_float = float(fAmount) # converting to float for math comparisons
			#if fAmount_float > 0:
			if fAmount_float > float(accountMaxAmount_res["maxAmount"]):
				return
			else:
				strAccountNumberFrom = order_doc.custom_fs_account_number
				strAccountNumberTo = fs_controller.fs_account

		else:
			order_doc.custom_fs_transfer_status = accountMaxAmount_res["Result"]
			return

		# FAPI stage-3
		transfer_token = None
		transfer_token = fs_controller.request_transfer_token()

		if not transfer_token:
			return

		#trans_date = order_doc.transaction_date
		trans_date = order_doc.delivery_date

		match order_doc.company:
			case "Pour Tous Canteen":
				strDescription = _("PTC/{0}/{1}").format(trans_date, order_doc.name)
			case "Pour Tous Purchasing Service":
				strDescription = _("PTPS/{0}/{1}").format(trans_date, order_doc.name)
			case "Auroville Bakery":
				strDescription = _("AVBK/{0}/{1}").format(trans_date, order_doc.name)
			case "AV Bakery Cafe":
				strDescription = _("AVBC/{0}/{1}").format(trans_date, order_doc.name)
			case "AV Bakery Cafe Townhall":
				strDescription = _("ABCT/{0}/{1}").format(trans_date, order_doc.name)
			case _:
				strDescription = _("{0}/{1}").format(trans_date, order_doc.name)

		payment_dict = {
			'reference_doctype': order_doc.doctype,
			'reference_docname': order_doc.name,
			"Customer Name": order_doc.customer_name,
			"Customer ID": order_doc.customer,
			#"Payment Name": "",
			#"Payment ID": ,
			"strAccountNumberFrom": strAccountNumberFrom,
			"strAccountNumberTo": strAccountNumberTo,
			"fAmount": str(fAmount),
			# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
			# string[0:5] extracts the first 5 chars of the string
			"strDescription": strDescription,
			"check": "Yes",
			"token": transfer_token
		}

		""" # if exists, fetch the existing integration request for this "Payment Entry" doc
		for integration_request_existing in frappe.get_all(
			"Integration Request",
			#filters={"status": "Queued", "integration_request_service": "FS", },
			filters={"status": ["in", {"Queued", "Failed"}], "integration_request_service": "FS", },
			fields=["name", "data"],
		):
			data = json.loads(integration_request_existing.data)
			if data["reference_docname"] == order_doc.name :
				integration_request = frappe.get_doc("Integration Request", integration_request_existing)
				#payment_dict_json = frappe.as_json(payment_dict, indent=1)
				#frappe.db.set_value("Integration Request", integration_request_existing.name, "data", payment_dict_json)
				break """

		# Create an "Integration Request" in case of a fresh transfer
		if not integration_request:
			# Create integration log
			integration_request = create_request_log(payment_dict, service_name="FS")

		# appending the integration_request name field as Transaction ID in strDescription
		payment_dict["strDescription"] = _("{0}/{1}").format(strDescription, integration_request.name)

		# FAPI stage-4
		addTransfer_res = fs_service_proxy.addTransfer(
			payment_dict["strAccountNumberFrom"],
			payment_dict["strAccountNumberTo"],
			payment_dict["fAmount"],
			payment_dict["strDescription"],
			payment_dict["check"],
			payment_dict["token"]
		)

		if addTransfer_res["Result"] == "OK":
			integration_request.status = "Completed"
			integration_request.save(ignore_permissions=True)
			#frappe.db.commit()

			order_doc.custom_fs_transfer_status = addTransfer_res["Result"]
			order_doc.save()

			# If FS transfer was successful,
			# then create a Payment Entry and reconcile with the Sales Invoice

			bank_account = get_bank_cash_account("FS", order_doc.company)

			pe = get_payment_entry(
				dt = order_doc.doctype,
				dn = order_doc.name,
				bank_account = bank_account["account"],
			)
			pe.mode_of_payment = "FS"
			pe.reference_no = payment_dict["strDescription"]
			#pe.reference_date = nowdate()
			#pe.paid_amount = pe.received_amount = fAmount
			pe.custom_fs_transfer_status = addTransfer_res["Result"]
			pe.custom_remarks = 1
			pe.remarks = addTransfer_res["Message"]

			pe.insert(ignore_permissions=True)
			pe.submit()
			frappe.db.commit()

			return addTransfer_res["Result"]

		else:
			integration_request.status = "Failed"
			integration_request.save(ignore_permissions=True)
			order_doc.custom_fs_transfer_status = addTransfer_res["Result"]
			order_doc.save()

			frappe.db.commit()
			#frappe.throw(addTransfer_res["Result"])

	except Exception as err:
		if integration_request:
			integration_request.status = "Failed"
			integration_request.error = str(err)
			integration_request.save(ignore_permissions=True)
			frappe.db.commit()

		frappe.msgprint(
			msg=str(err),
			title='Error',
		)
		return
	
	return


@frappe.whitelist()
def fetch_fs_credit_bills():
	""" match year:
		case "Current":
			year = date.today().year
		case "Previous":
			year = date.today().year - 1
		case "Previous-1":
			year = date.today().year - 2 """

	return frappe.db.sql(
    	"""
		SELECT name
		FROM `tabSales Invoice`
		WHERE docstatus = 1 AND status IN ("Unpaid", "Overdue", "Partly Paid", "Return")
		AND custom_fs_transfer_status IN ("Insufficient Funds", "Pending", "Retry-Payment", "Failed", "ERR101: Account number (to) '0373' is invalid.", "ERR095: Account (from) "102142" not Active (Suspended, Locked or Closed)");
	    """,
        #as_dict=1,
    )

@frappe.whitelist()
def add_transfer_fs_credit_bill(bill):
	invoice_doc = frappe.get_doc("Sales Invoice", bill)

	# if exists, fetch the existing integration request
	integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": invoice_doc.name}, "name")
	if integration_request_existing and invoice_doc.custom_fs_transfer_status != "Retry-Payment":
		int_req_doc = frappe.get_doc("Integration Request", integration_request_existing)
		status_msg = int_req_doc.name + ": check FS tx status"

		invoice_doc.custom_fs_transfer_status = status_msg
		invoice_doc.save()
		frappe.db.commit()
		return

		""" if int_req_doc.status == 'Completed':
			frappe.msgprint(
				msg=_("Duplicate Payment Request: Invoice {0} was paid with Integration Request {1}").format(invoice_doc.name, integration_request_existing),
				title='Error',
			)
		else:
			invoice_doc.custom_fs_transfer_status = int_req_doc.status
			invoice_doc.save()
			frappe.db.commit()
		return """

	cust_fs_acc_number = frappe.get_value("Customer", invoice_doc.customer, "custom_fs_account_number")
	if not cust_fs_acc_number:
		frappe.throw(str(invoice_doc.customer))

	fs_controller = frappe.get_doc("FS Settings")

	integration_request = None # initialising before the try except statement, as it is referenced in the except clause

	try:
		# FAPI stage-1
		login_res = fs_controller.fapi_login()
		if login_res["Result"] != "OK":
			frappe.msgprint(
				msg=login_res["Result"],
				title='Error',
			)
			return

		if fs_controller.production:
			fs_service_proxy = fs_controller.production_service
		else:
			fs_service_proxy = fs_controller.staging_service

		fAmount = invoice_doc.outstanding_amount

		# FAPI stage-2
		accountMaxAmount_res = fs_service_proxy.getAccountMaxAmount(cust_fs_acc_number)
		if accountMaxAmount_res["Result"] == "OK":
			#accountMaxAmount = float(accountMaxAmount_res["maxAmount"])
			#if fAmount > accountMaxAmount:

			fAmount_float = float(fAmount) # converting to float in order to do check for negative amounts below
			if fAmount_float > 0:
				if fAmount_float > float(accountMaxAmount_res["maxAmount"]):
					invoice_doc.custom_fs_transfer_status = "Insufficient Funds"
					invoice_doc.save()
					frappe.db.commit()
					return
					# for incremental debits in case of insufficent funds for the full outstanding amount
					#fAmount = float(accountMaxAmount_res["maxAmount"])
				else:
					strAccountNumberFrom = cust_fs_acc_number
					strAccountNumberTo = fs_controller.fs_account
					#fAmount = invoice_doc.outstanding_amount

			else:
				# in case of returns, the amount will be a negative value,
				# hence convert it to postive, and swap the from/to FS account numbers, to make a return transfer
				fAmount = abs(fAmount_float)
				#frappe.throw(str(fAmount))
				strAccountNumberFrom = fs_controller.fs_account
				strAccountNumberTo = cust_fs_acc_number

		else:
			frappe.msgprint(
				msg=accountMaxAmount_res["Result"],
				title='Error',
			)
			invoice_doc.custom_fs_transfer_status = accountMaxAmount_res["Result"]
			invoice_doc.save()
			frappe.db.commit()
			return

		# FAPI stage-3
		transfer_token = fs_controller.request_transfer_token()

		if transfer_token:
			if invoice_doc.custom_transaction_date:
				trans_date = invoice_doc.custom_transaction_date
			else:
				#trans_date = invoice_doc.creation.date()
				trans_date = invoice_doc.posting_date

			match invoice_doc.company:
				case "Pour Tous Canteen":
					strDescription = _("PTC/{0}/{1}").format(trans_date, invoice_doc.name)
				case "Pour Tous Purchasing Service":
					strDescription = _("PTPS/{0}/{1}").format(trans_date, invoice_doc.name)
				case "Auroville Bakery":
					strDescription = _("AVBK/{0}/{1}").format(trans_date, invoice_doc.name)
				case "AV Bakery Cafe":
					strDescription = _("AVBC/{0}/{1}").format(trans_date, invoice_doc.name)
				case "AV Bakery Cafe Townhall":
					strDescription = _("ABCT/{0}/{1}").format(trans_date, invoice_doc.name)
				case _:
					strDescription = _("{0}/{1}").format(trans_date, invoice_doc.name)

			payment_dict = {
				'reference_doctype': invoice_doc.doctype,
				'reference_docname': invoice_doc.name,
				"Customer Name": invoice_doc.customer_name,
				"Customer ID": invoice_doc.customer,
				#"Payment Name": "",
				#"Payment ID": ,
				"strAccountNumberFrom": strAccountNumberFrom,
				"strAccountNumberTo": strAccountNumberTo,
				"fAmount": str(fAmount),
				# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
				# string[0:5] extracts the first 5 chars of the string
				"strDescription": strDescription,
				"check": "Yes",
				"token": transfer_token
			}

			# if exists, fetch the existing integration request for this "Payment Entry" doc
			""" for integration_request_existing in frappe.get_all(
				"Integration Request",
				#filters={"status": "Queued", "integration_request_service": "FS", },
				filters={"status": ["in", {"Queued", "Failed"}], "integration_request_service": "FS", },
				fields=["name", "data"],
			):
				data = json.loads(integration_request_existing.data)
				if data["reference_docname"] == invoice_doc.name :
					integration_request = frappe.get_doc("Integration Request", integration_request_existing)
					#payment_dict_json = frappe.as_json(payment_dict, indent=1)
					#frappe.db.set_value("Integration Request", integration_request_existing.name, "data", payment_dict_json)
					break

			# Create an "Integration Request" in case of a fresh transfer
			if not integration_request: """

			# Create integration log
			integration_request = create_request_log(payment_dict, service_name="FS")

			# appending the integration_request name field as Transaction ID in strDescription
			payment_dict["strDescription"] = _("{0}/{1}").format(strDescription, integration_request.name)

			# FAPI stage-4
			addTransfer_res = fs_service_proxy.addTransfer(
				payment_dict["strAccountNumberFrom"],
				payment_dict["strAccountNumberTo"],
				payment_dict["fAmount"],
				payment_dict["strDescription"],
				payment_dict["check"],
				payment_dict["token"]
			)

			if addTransfer_res["Result"] == "OK":
				integration_request.status = "Completed"
				integration_request.save(ignore_permissions=True)
				#frappe.db.commit()

				invoice_doc.custom_fs_transfer_status = "OK - Paid"
				invoice_doc.save()
				frappe.db.commit()

				# If FS transfer was successful,
				# then create a Payment Entry and reconcile with the Sales Invoice

				bank_account = get_bank_cash_account("FS", invoice_doc.company)

				pe = get_payment_entry(
					dt = invoice_doc.doctype,
					dn = invoice_doc.name,
					bank_account = bank_account["account"],
				)
				pe.mode_of_payment = "FS"
				pe.reference_no = payment_dict["strDescription"]
				pe.reference_date = nowdate()
				#pe.paid_amount = pe.received_amount = fAmount
				pe.custom_fs_transfer_status = addTransfer_res["Result"]
				pe.custom_remarks = 1
				pe.remarks = addTransfer_res["Message"]

				pe.insert(ignore_permissions=True)
				pe.submit()

				return addTransfer_res["Result"]

			else:
				integration_request.status = "Failed"
				integration_request.save(ignore_permissions=True)
				invoice_doc.custom_fs_transfer_status = addTransfer_res["Result"]
				invoice_doc.save()
				frappe.db.commit()
				frappe.msgprint(
					msg=addTransfer_res["Result"],
					title='Error',
				)

				return

		else:
			frappe.msgprint(
				msg="FS transfer token not received",
				title='Error',
			)
			return

	except Exception as err:
		if integration_request:
			integration_request.status = "Failed"
			integration_request.save(ignore_permissions=True)
			frappe.db.commit()

		frappe.msgprint(
			msg=str(err),
			title='Error',
		)
		return


#@frappe.whitelist()
#def add_transfer_fs_draft_bills():
	# for Offline FS bills

	""" match year:
		case "Current":
			year = date.today().year
		case "Previous":
			year = date.today().year - 1
		case "Previous-1":
			year = date.today().year - 2 """

#	draft_fs_bills = frappe.db.sql(
#		"""
#		SELECT name
#		FROM `tabSales Invoice`
#		WHERE docstatus = 0 AND NOT custom_fs_transfer_status = "OK" AND custom_fs_transfer_status IS NOT NULL
#		""",
#		as_dict=1,
#	)

	""" if draft_fs_bills:
		fs_controller = frappe.get_doc("FS Settings")
		#fs_bulk_trans_doc = frappe.get_doc("FS Bulk Transfer", doc_name.replace("new-fs-bulk-transfer-", ""))

		for bill in draft_fs_bills:
			invoice_doc = frappe.get_doc("Sales Invoice", bill)
	
			integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": invoice_doc.name, "status": "completed"}, "name")
			if integration_request_existing:
				continue

			cust_fs_acc_number = frappe.get_value("Customer", invoice_doc.customer, "custom_fs_account_number")
			if not cust_fs_acc_number:
				frappe.throw(str(invoice_doc.customer))

			login_res = fs_controller.fapi_login()
			if login_res["Result"] == "OK":

				if fs_controller.production:
					fs_service_proxy = fs_controller.production_service
				else:
					fs_service_proxy = fs_controller.staging_service

				fAmount = invoice_doc.total
				if fAmount > 0:
					strAccountNumberFrom = cust_fs_acc_number
					strAccountNumberTo = fs_controller.fs_account

					accountMaxAmount_res = fs_service_proxy.getAccountMaxAmount(strAccountNumberFrom)
					if accountMaxAmount_res["Result"] == "OK":
						#accountMaxAmount = float(accountMaxAmount_res["maxAmount"])
						#if fAmount > accountMaxAmount and accountMaxAmount != -1:
						if fAmount > float(accountMaxAmount_res["maxAmount"]):
							invoice_doc.custom_fs_transfer_status = "Insufficient Funds"
							invoice_doc.custom_fs_account_number = strAccountNumberFrom
							invoice_doc.outstanding_amount = fAmount # for "Credit Sale"
							invoice_doc.due_date = get_last_day_of_Month()

							invoice_doc.save()
							invoice_doc.submit()

							continue
					else:
						frappe.throw(accountMaxAmount_res["Result"])

				else:
					# in case of returns, the amount will be a negative value,
					# hence convert it to postive, and swap the from/to FS account numbers, to make a return transfer
					fAmount = abs(fAmount)
					strAccountNumberFrom = fs_controller.fs_account
					strAccountNumberTo = cust_fs_acc_number

				transfer_token = fs_controller.request_transfer_token()
				if transfer_token:
					if invoice_doc.custom_transaction_date:
						trans_date = invoice_doc.custom_transaction_date
					else:
						trans_date = invoice_doc.creation.date()

					match invoice_doc.company:
						case "Pour Tous Canteen":
							strDescription = _("PTC/{0}/{1}").format(trans_date, invoice_doc.name)
						case "Pour Tous Purchasing Service":
							strDescription = _("PTPS/{0}/{1}").format(trans_date, invoice_doc.name)
						case "Auroville Bakery":
							strDescription = _("AVBK/{0}/{1}").format(trans_date, invoice_doc.name)
						case _:
							strDescription = _("{0}/{1}").format(trans_date, invoice_doc.name)

					payment_dict = {
						'reference_doctype': invoice_doc.doctype,
						'reference_docname': invoice_doc.name,
						"Customer Name": invoice_doc.customer_name,
						"Customer ID": invoice_doc.customer,
						#"Payment Name": "",
						#"Payment ID": ,
						"strAccountNumberFrom": strAccountNumberFrom,
						"strAccountNumberTo": strAccountNumberTo,
						"fAmount": str(fAmount),
						# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
						# string[0:5] extracts the first 5 chars of the string
						"strDescription": strDescription,
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
					payment_dict["strDescription"] = _("{0}/{1}").format(strDescription, integration_request.name)

					# adding a dummy remark here in order to be able to save the invoice before initiating an FS payment
					# so that in case this iteration breaks due to an Item batch QTY insufficient message - the FS transaction should not go through before that.
					try:
						invoice_doc.remarks = "Saving this doc, before attempting an FS Transaction"
						invoice_doc.save()
					except:
						frappe.throw("Error (Could be due to Batch availability)")

					addTransfer_res = fs_service_proxy.addTransfer(
						payment_dict["strAccountNumberFrom"],
						payment_dict["strAccountNumberTo"],
						payment_dict["fAmount"],
						payment_dict["strDescription"],
						payment_dict["check"],
						payment_dict["token"]
					)

					if addTransfer_res["Result"] == "OK":
						integration_request.status = "Completed"
						integration_request.save(ignore_permissions=True)
						frappe.db.commit()

						#invoice_doc.payments[0].mode_of_payment = "FS"
						#invoice_doc.payments[0].amount = fAmount
						for payment in invoice_doc.payments:
							if payment.mode_of_payment == "FS":
								payment.amount = fAmount
								break

						invoice_doc.paid_amount = fAmount
						invoice_doc.custom_fs_transfer_status = addTransfer_res["Result"]
						invoice_doc.custom_fs_account_number = payment_dict["strAccountNumberFrom"]
						invoice_doc.remarks = addTransfer_res["Message"]

						invoice_doc.save()
						invoice_doc.submit()

					else:
						integration_request.status = "Failed"
						integration_request.save(ignore_permissions=True)
						frappe.db.commit()

						invoice_doc.custom_fs_transfer_status = addTransfer_res["Result"]
						invoice_doc.remarks = addTransfer_res["Message"]
						invoice_doc.due_date = get_last_day_of_Month()

						invoice_doc.save()

				else:
					frappe.throw("FS transfer token not received")

			else:
				frappe.throw(login_res["Result"])

	return """