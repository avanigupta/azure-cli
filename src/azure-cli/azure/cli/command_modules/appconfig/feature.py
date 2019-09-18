# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

# pylint: disable=line-too-long

import io
import json
import sys
import time
import re

import chardet
import javaproperties
import yaml
from itertools import chain
from jsondiff import JsonDiffer
from knack.log import get_logger
from knack.util import CLIError

from ._utils import resolve_connection_string, user_confirmation, error_print
from ._azconfig.azconfig_client import AzconfigClient
from ._azconfig.constants import StatusCodes
from ._azconfig.exceptions import HTTPException
from ._azconfig.models import (KeyValue,
                               ModifyKeyValueOptions,
                               QueryKeyValueCollectionOptions,
                               QueryKeyValueOptions)
from ._featuremodels import (map_keyvalue_to_featureflagdisplay,
                            map_valuestr_to_valuedict)



logger = get_logger(__name__)
FEATURE_FLAG_PREFIX = ".appconfig.featureflag/"
FEATURE_FLAG_CONTENT_TYPE = "application/vnd.microsoft.appconfig.ff+json;charset=utf-8"

# Feature commands

def set_feature(cmd,
                feature,
                name=None,
                label=None,
                description=None,
                yes=False,
                connection_string=None):
    
    key = FEATURE_FLAG_PREFIX + feature
    content_type = FEATURE_FLAG_CONTENT_TYPE
    tags={}
    default_conditions = {}
    default_conditions['client_filters'] = []
    # when creating a new Feature flag, these defaults will be used
    value = {
        "id": feature,
        "description": description,
        "enabled": False,
        "label": label,
        "conditions": default_conditions
    }

    # Feature Flag object structures
    """
    KeyValue Object
    {
        "etag": null,          
        "key": key,
        "label": label,
        "content_type": content_type,
        "value": value,
        "tags": {},             # Feature flags dont have tags, always null
        "locked": false,
        "last_modified": null 
    }

    where "value" is a valid JSON string that can be converted to this dictionary:
    {
        "id": feature,
        "description": description,
        "enabled": false,       # Feature flags disabled by default
        "label": label,
        "conditions": {
            "client_filters": []
        }
    }

    Displayed to the user as FeatureFlagDisplay object
    {
        "conditions": {
		    "client_filters": [
                "{'name': 'new_filter', 'parameters': {'name1': 'val1', 'name2': 'val2'}}",
                "{'name': 'new_filter2', 'parameters': {}}"
		    ]
	    },
        "description": description,
        "key": feature,
        "label": label,
        "lastModified": "2019-09-11T19:07:27+00:00",
        "locked": false,
        "state": "conditional"
    }

    """

    connection_string = resolve_connection_string(cmd, name, connection_string)
    azconfig_client = AzconfigClient(connection_string)
    retry_times = 3
    retry_interval = 1
    query_options = QueryKeyValueOptions(label=label)
    for i in range(0, retry_times):
        retrieved_kv = azconfig_client.get_keyvalue(key, query_options)
        try:
            if retrieved_kv is None:
                set_kv = KeyValue(key, json.dumps(value, indent=2), label, tags, content_type)
            else:
                # User can only update description if the key already exists 
                # label is already the same as retrieved key
                    value = map_valuestr_to_valuedict(retrieved_kv)
                    value['description']=description
                    set_kv = KeyValue(key=key,
                                label=label,
                                value=json.dumps(value, indent=2),
                                content_type=content_type,
                                tags=retrieved_kv.tags if retrieved_kv.tags else tags)
                    set_kv.etag = retrieved_kv.etag
                    set_kv.last_modified = retrieved_kv.last_modified
        
            # Convert KeyValue object to required Feature Flag Display format
            feature_flag_display = map_keyvalue_to_featureflagdisplay(set_kv, show_conditions=True)
            entry = json.dumps(feature_flag_display.__dict__, indent=2, sort_keys=True)

        except ValueError as exception:
            raise CLIError(str(exception))

        except Exception as exception:
            raise CLIError(str(exception))

        confirmation_message = "Are you sure you want to set the feature flag: \n" + entry + "\n"
        user_confirmation(confirmation_message, yes)

        try:
            updated_key_value = azconfig_client.add_keyvalue(set_kv, ModifyKeyValueOptions()) if set_kv.etag is None else azconfig_client.update_keyvalue(set_kv, ModifyKeyValueOptions())
            return map_keyvalue_to_featureflagdisplay(keyvalue=updated_key_value, show_conditions=True)
        except HTTPException as exception:
            if exception.status == StatusCodes.PRECONDITION_FAILED:
                logger.debug(
                    'Retrying setting %s times with exception: concurrent setting operations', i + 1)
                time.sleep(retry_interval)
            else:
                raise CLIError(str(exception))
        except Exception as exception:
            raise CLIError(str(exception))
    raise CLIError(
        "Failed to set the feature flag '{}' due to a conflicting operation.".format(key))


def delete_feature(cmd,
                feature,
                name=None,
                label=None,
                yes=False,
                connection_string=None):
    connection_string = resolve_connection_string(cmd, name, connection_string)
    azconfig_client = AzconfigClient(connection_string)

    delete_one_version_message = "Are you sure you want to delete the feature '{}'".format(feature)
    confirmation_message = delete_one_version_message
    user_confirmation(confirmation_message, yes)

    try:
        retrieved_keyvalues = __list_all_keyvalues( cmd,
                                                    feature=feature, 
                                                    name=name,
                                                    label=label, 
                                                    connection_string=connection_string)
    except HTTPException as exception:
        raise CLIError('Delete operation failed. ' + str(exception))

    deleted_kv = []
    not_deleted_kv = []
    http_exception = None
    for entry in retrieved_keyvalues:
        try:
            deleted_kv.append(azconfig_client.delete_keyvalue(entry, ModifyKeyValueOptions()))
        except HTTPException as exception:
            not_deleted_kv.append(entry.__dict__)
            http_exception = exception
        except Exception as exception:
            raise CLIError(str(exception))

    if not_deleted_kv:
        if deleted_kv:
            # Log partial success - display feature flags that failed to be deleted
            logger.error('Delete operation partially succeeded. Unable to delete the following keys: \n')
            not_deleted_ff_display = []
            for failed_kv in not_deleted_kv:
                failed_ff = map_keyvalue_to_featureflagdisplay(failed_kv, show_conditions=False)
                not_deleted_ff_display.append(failed_ff)
                logger.error(json.dumps(failed_ff.__dict__, indent=2, sort_keys=True))
        else:
            raise CLIError('Delete operation failed.' + str(http_exception))
    
    # Convert result list of KeyValue to ist of FeatureFlagDisplay
    deleted_ff_display = []
    for success_kv in deleted_kv:
        success_ff = map_keyvalue_to_featureflagdisplay(success_kv, show_conditions=False)
        deleted_ff_display.append(success_ff)
    
    return deleted_ff_display


def show_feature(cmd,
                feature,
                name=None,
                label=None,
                fields=None,
                connection_string=None):     
    key = FEATURE_FLAG_PREFIX + feature
    connection_string = resolve_connection_string(cmd, name, connection_string)
    azconfig_client = AzconfigClient(connection_string)
    
    # If user has specified fields, we still get all the fields and then filter what we need from the response. 
    query_option = QueryKeyValueOptions(label=label, fields=None)
    
    try:
        key_value = azconfig_client.get_keyvalue(key, query_option)
        if key_value is None:
            raise CLIError("The Feature Flag {} does not exist.".format(feature))
        
        feature_flag_display = map_keyvalue_to_featureflagdisplay(keyvalue=key_value, show_conditions=True)

        if fields:
            partial_ff = {}
            for field in fields:
                partial_ff[field.name.lower()] = getattr(feature_flag_display, field.name.lower(), "")
            return partial_ff
        else:
            return feature_flag_display
    except Exception as exception:
        raise CLIError(str(exception))


def list_feature(cmd,
                feature,
                name=None,
                label=None,
                fields=None,
                connection_string=None,
                top=None,
                all_=False):
    retrieved_keyvalues = __list_all_keyvalues( cmd,
                                                feature=feature, 
                                                name=name,
                                                label=label, 
                                                connection_string=connection_string)
    retrieved_featureflagdisplay = []
    for kv in retrieved_keyvalues:
        retrieved_featureflagdisplay.append(map_keyvalue_to_featureflagdisplay(keyvalue=kv, show_conditions=True))
    filtered_ff_display = []
    count = 0

    if all:
        top = float('inf')
    elif top is None:
        top = 100

    for ff_display in retrieved_featureflagdisplay:
        if fields:
            partial_ff = {}
            for field in fields:
                partial_ff[field.name.lower()] = getattr(ff_display, field.name.lower(), "")
            filtered_ff_display.append(partial_ff)
        else:
            filtered_ff_display.append(ff_display)
        count += 1
        if count >= top:
            break
    return filtered_ff_display


def __list_all_keyvalues(cmd,
                        feature,
                        name=None,
                        label=None,
                        connection_string=None):
    connection_string = resolve_connection_string(cmd, name, connection_string)
    azconfig_client = AzconfigClient(connection_string)

    
    # We dont support listing comma separated keys and ned to fail with appropriate error
    # (?<!\\)    Matches if the preceding character is not a backslash
    # (?:\\\\)*  Matches any number of occurrences of two backslashes
    # ,          Matches a comma
    unescaped_comma_regex = re.compile(r'(?<!\\)(?:\\\\)*,')
    if unescaped_comma_regex.search(feature):
        raise CLIError("Comma separated feature names are not supported. Please provide escaped string if your feature name contains comma. \nSee \"az appconfig feature list -h\" for correct usage.")
    
    # Filtering keys on these patterns needs to happen on client side after getting all keys
    # If user provides *abc or *abc* or * -> get all feature flags, then filter based on the ID (feature flag name) of results
    custom_key_pattern = "*"
    if feature.startswith("*"):
        custom_key_pattern = "*"
    else:
        custom_key_pattern = feature

    internal_key = FEATURE_FLAG_PREFIX + custom_key_pattern

    # If user has specified fields, we still get all the fields and then filter what we need from the response. 
    query_option = QueryKeyValueCollectionOptions(key_filter=internal_key,
                                                  label_filter=QueryKeyValueCollectionOptions.empty_label if label is None else label,
                                                  fields=None)
    try:
        retrieved_kv = azconfig_client.get_keyvalues(query_option)
        if custom_key_pattern == feature:
            return retrieved_kv
        return __custom_key_filtering(retrieved_kv=retrieved_kv, user_key_filter=feature)
    except Exception as exception:
        raise CLIError(str(exception))


def __custom_key_filtering(retrieved_kv, user_key_filter):
    # Client side Filtering based on user specified pattern
    filtered_kv = []
    try:
        # User requested to view all Feature Flags
        if user_key_filter == "*":
            return retrieved_kv

        # User requested to view Feature Flags that "Contain" certain characters
        if user_key_filter.startswith("*") and user_key_filter.endswith("*"):
            for kv in retrieved_kv:
                internal_key = getattr(kv, 'key')
                feature_name = internal_key[len(FEATURE_FLAG_PREFIX):]
                if user_key_filter[1:-1] in feature_name:
                    filtered_kv.append(kv)
            return filtered_kv
                

        # User requested to view Feature Flags that "End With" certain characters
        if user_key_filter.startswith("*"):
            for kv in retrieved_kv:
                internal_key = getattr(kv, 'key')
                feature_name = internal_key[len(FEATURE_FLAG_PREFIX):]
                if  feature_name.endswith(user_key_filter[1:]):
                    filtered_kv.append(kv)
            return filtered_kv

    except AttributeError as exception:
        logger.error("Could not find 'key' attribute in the retrieved Key-Value data.")
        raise CLIError(str(exception))

    except Exception as exception:
        raise CLIError(str(exception))

    return filtered_kv

