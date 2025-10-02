# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method, nowdate
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway

import requests, json
import asyncio


class UPISettings(Document):
	supported_currencies = ["INR"]

	check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CheckStatus"
	push_txs_url = "https://iciciapi.lyra-network.in/erpservice/ERP/PushTxn"
	cancel_txs_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CancelTxn"
	callback_check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CallbackStatusCheck"

	webhook_callback_url = "https://pourtous-av.in/api/method/payments.payment_gateways.doctype.upi_settings.upi_settings.icici_webhook_callback"


	def	validate(self):
		create_payment_gateway("UPI")
		call_hook_method("payment_gateway_enabled", gateway="UPI")

		self.erp_callback_api_token = self.erp_callback_api_key + ':' + self.get_password(fieldname="erp_callback_api_secret", raise_exception=False)
		#if not self.flags.ignore_mandatory:
		#	self.validate_upi_credentials()

	def	validate_upi_credentials(self):
		pass

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)

	def checkStatus(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}

			r = s.post(self.check_status_url, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			return r.json()

			#if r.json().get('ResponseCode') == '01':
			#	return "OK"
			#else:
				#frappe.msgprint(r.json().get('ResponseDesc'))
			#	return r.json().get('ResponseDesc')
				#r.raise_for_status()


	def pushTxn(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}
			
			r = s.post(self.push_txs_url, data=json.dumps(data), timeout=40)
			#frappe.throw(str(r.json()))

			if r.json().get('ResponseCode') != '00':
				frappe.msgprint(r.json().get('ResponseDesc'))

			return r.json()
			#r.raise_for_status()


	def checkCallbackStatus(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}

			r = s.post(self.callback_check_status_url, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			return r.json()

			# return {
			# 	"ResponseCode": r.json().get("ResponseCode"),
			# 	"ResponseDesc": r.json().get("ResponseDesc")
			# }
			# if r.json().get('ResponseCode') == '00' and r.json().get('ResponseDesc') == 'SUCCESS':
			# 	return 'SUCCESS'
			# else:
			# 	return r.json().get('ResponseDesc')


	def cancelTxn(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}

			r = s.post(self.cancel_txs_url, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			return r.json()
			#if r.json().get('ResponseCode') == '00':
			#	return "OK"
			#else:
				#frappe.msgprint(r.json().get('ResponseDesc'))
				#return r.json().get('ResponseDesc')
				#r.raise_for_status()


@frappe.whitelist()
def icici_check_service():
	icici_controller = frappe.get_doc("UPI Settings")

	# Sample Data to check service availability
	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": 16,
		"bill_no": "123456",
		"erp_tran_id": "240230025530444",
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	return icici_controller.checkStatus(data)


@frappe.whitelist()
def check_status(bill_no, tran_type, erp_tran_id):
	icici_controller = frappe.get_doc("UPI Settings")

	# Sample Data to check service availability
	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": tran_type,
		"bill_no": bill_no,
		"erp_tran_id": erp_tran_id,
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	return icici_controller.checkStatus(data)


# @frappe.whitelist(allow_guest=True)
# def icici_webhook_callback_test():
# 	data = frappe.request.data
# 	upi_response = json.loads(data)
# 	with open('webhook_req_log.txt', 'w') as file:
# 		file.write(str(upi_response))


#to be used when webhook is active
@frappe.whitelist()
def icici_webhook_callback(integration_request_name=None):
	if integration_request_name:
		webhook_request_log_existing = frappe.get_value("Webhook Request Log", {
			"reference_document": integration_request_name,
		}, "name")
	
		if webhook_request_log_existing:
			webhook_request_log = frappe.get_doc("Webhook Request Log", webhook_request_log_existing)
			return {
				"custom_upi_transfer_status": webhook_request_log.response.get("TxnStatus"),
				"TranType": webhook_request_log.response.get("TranType"),
				"ErpTranId": webhook_request_log.response.get("ErpTranId"),
				"TranId": webhook_request_log.response.get("TranId")
			}

	data = frappe.request.data
	upi_response = json.loads(data)

	if upi_response.get("TxnStatus") == "SUCCESS":
		icici_controller = frappe.get_doc("UPI Settings")

		webhook_req_log = frappe.new_doc("Webhook Request Log")
		webhook_req_log.user = icici_controller.erp_callback_user_id

		if "ErpTranId" in upi_response:
			# store the Integration Request ID as reference_document in the webhook_req_log
			webhook_req_log.reference_document = upi_response.get("ErpTranId")
		webhook_req_log.response = data
		webhook_req_log.insert()

		if integration_request_name:
			integration_request = frappe.get_doc("Integration Request", integration_request_name)
			integration_request.output = data.json()
			integration_request.status == "Completed"
			integration_request.save(ignore_permissions=True)

		if "ErpTranId" in upi_response and upi_response.get("ErpTranId") == integration_request_name:
			return {
				"custom_upi_transfer_status": upi_response.get("TxnStatus"),
				"TranType": upi_response.get("TranType"),
				"ErpTranId": upi_response.get("ErpTranId"),
				"TranId": upi_response.get("TranId")
			}


	""" if integration_request_name and data.get("ErpTranId") == integration_request.name:
		integration_request = frappe.get_doc("Integration Request", integration_request_name)
		integration_request.output = data.json()

		if data.get("TxnStatus") == "SUCCESS":
			integration_request.status == "Completed"
			integration_request.save(ignore_permissions=True)
			return "SUCCESS"
		else:
			integration_request.status == "Failed"
			integration_request.save(ignore_permissions=True)
			return "FAILED" """


@frappe.whitelist()
def get_upi_confirmation(bill_no, tran_type, erp_tran_id, before_push_txn=False):
	# use these statements(to be matured), when the webhook is active:-
	# integration_request = frappe.get_doc("Integration Request", erp_tran_id)
	# webhook_req_log_list = frappe.get_list("Webhook Request Log", {"reference_document": erp_tran_id})
	# if len(webhook_req_log_list) > 0:
	# 	for webhook_req_log_name in webhook_req_log_list:
	# 		webhook_req_log = frappe.get_doc("Webhook Request Log", webhook_req_log_name)
	# 		if webhook_req_log.get("response").get("TxnStatus") == "SUCCESS":
	# 			integration_request.status == "Completed"
	# 			return "SUCCESS"
	# else:

	icici_controller = frappe.get_doc("UPI Settings")
	integration_request = frappe.get_doc("Integration Request", erp_tran_id)

	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": tran_type,
		"bill_no": bill_no[-6:],
		"erp_tran_id": erp_tran_id,
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}
	#frappe.throw(str(data))
	# use the statement below, when the webhook is active
	#return icici_webhook_callback(integration_request.name)

	res = icici_controller.checkCallbackStatus(data)
	#frappe.throw(str(res))
	if before_push_txn: # if local call from push_txn below
		return res

	if "ResponseCode" and "ResponseDesc" in res:
		if res.get("ResponseCode") == "00" or res.get("ResponseDesc") == "SUCCESS":
			integration_request.output = res
			integration_request.status == "Completed"
			integration_request.save(ignore_permissions=True)
			return {
				"ResponseCode": res.get("ResponseCode"),
				"ResponseDesc": res.get("ResponseDesc"),
				"ErpTranId": res.get("RspData").get("ErpTranId"),
				"TranId": res.get("RspData").get("TranId")
			}

		elif res.get("ResponseCode") == "02" or res.get("ResponseDesc") == "Invalid MID/TID.":
			integration_request.status == "Failed"
			integration_request.save(ignore_permissions=True)
			return res
	
	# else:
	# 	integration_request.status == "Failed"
	# 	integration_request.save(ignore_permissions=True)
	# else:
	# 	frappe.throw(str(res))


@frappe.whitelist()
def push_txn(invoice_doc, tran_type, amount, tip):
	invoice_dict = json.loads(invoice_doc)

	integration_request = None
	icici_controller = frappe.get_doc("UPI Settings")

	integration_request_existing = frappe.get_value("Integration Request", {
		"reference_docname": invoice_dict["name"],
		"status": "Completed",
	}, "name")

	if integration_request_existing:
		integration_request = frappe.get_doc("Integration Request", integration_request_existing)
		data = json.loads(integration_request.output)
		
		return {
			"custom_upi_transfer_status": data.get("ResponseDesc"),
			"ResponseCode": data.get("ResponseCode"),
			"ResponseDesc": data.get("ResponseDesc"),
			"ErpTranId": data.get("RspData").get("ErpTranId"),
			"TranId": data.get("RspData").get("TranId")
		}

	else:
		integration_request_existing_list = frappe.get_list("Integration Request", {
			"reference_docname": invoice_dict["name"],
			"status": "Queued",
			"request_description": "Success" # transaction requests that were successfully received by ICICI
		}, "name")

		if len(integration_request_existing_list) > 0:

			for integration_request_existing in integration_request_existing_list:
				#res = check_status(invoice_dict["name"], tran_type, integration_request_existing["name"])
				res = get_upi_confirmation(invoice_dict["name"], tran_type, integration_request_existing["name"], before_push_txn=True)
				#frappe.throw(str(res))

				if res:
					if res.get("ResponseCode") == "00" or res.get("ResponseDesc") == "SUCCESS":
						return {
							"custom_upi_transfer_status": data.get("ResponseDesc"),
							"ResponseCode": res.get("ResponseCode"),
							"ResponseDesc": res.get("ResponseDesc"),
							"ErpTranId": res.get("RspData").get("ErpTranId"),
							"TranId": res.get("RspData").get("TranId")
						}
					
					elif res.get("ResponseCode") == "02" or res.get("ResponseDesc") == "Invalid MID/TID.":
						integration_request_existing.status == "Failed"
						integration_request_existing.save(ignore_permissions=True)
						return res

					ir = frappe.db.get_value("Integration Request", integration_request_existing["name"], "creation")
					# delete the matching old records
					if ir.strftime('%Y-%m-%d') < nowdate():
						frappe.db.delete_doc("Integration Request", integration_request_existing["name"])

	payment_dict = {
		'reference_doctype': invoice_dict["doctype"],
		'reference_docname': invoice_dict["name"],
		"Customer Name": invoice_dict["customer_name"],
		"Customer ID": invoice_dict["customer"],
		"tran_type": tran_type,
		"amount": amount,
		"tip": tip,
		# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
		# string[0:5] extracts the first 5 chars of the string
		"check": "Yes",
	}

	# Create integration log
	integration_request = create_request_log(payment_dict, service_name="UPI")

	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": tran_type,
		"amount": amount,
		"bill_no": invoice_dict["name"][-6:],
		"tip": tip,
		"erp_tran_id": integration_request.name,
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	res = icici_controller.pushTxn(data)
	#frappe.throw(str(res))

	if res.get("ResponseCode") == "00" or res.get("ResponseDesc") == "Success":
		integration_request.status = "Queued"
		integration_request.request_description = "Success"
	else:
		integration_request.status = "Failed"

	integration_request.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"ResponseCode": res.get("ResponseCode"),
		"ResponseDesc": res.get("ResponseDesc"),
		"erp_tran_id": integration_request.name
	}


@frappe.whitelist()
def cancel_txn(bill_no, tran_type, erp_tran_id):
	icici_controller = frappe.get_doc("UPI Settings")

	integration_request = frappe.get_doc("Integration Request", erp_tran_id)

	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": tran_type,
		"bill_no": bill_no[-6:],
		"erp_tran_id": erp_tran_id,
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	res = icici_controller.cancelTxn(data)

	if res:
		if res.get("RspCode") == "00" or res.get("RspDesc") == "Success":
			integration_request.status == "Cancelled"
			integration_request.save(ignore_permissions=True)
			return res
