# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from enum import Enum
import json
from knack.util import CLIError
from knack.log import get_logger

# pylint: disable=too-few-public-methods
# pylint: disable=too-many-instance-attributes

logger = get_logger(__name__)
FEATURE_FLAG_PREFIX = ".appconfig.featureflag/"

class FeatureState(Enum):
    OFF = 1
    ON = 2
    CONDITIONAL = 3


class FeatureQueryFields(Enum):
    KEY = 0x001
    LABEL = 0x002
    LAST_MODIFIED = 0x020
    LOCKED = 0x040
    STATE = 0x100
    DESCRIPTION = 0x200
    CONDITIONS = 0x400
    ALL = KEY | LABEL | LAST_MODIFIED | LOCKED | STATE | DESCRIPTION | CONDITIONS


class FeatureFlagDisplay(object):
    '''
    Feature Flag schema as displayed to the user.

    :ivar str key:
        FeatureName (key) of the entry.
    :ivar str label:
        Label of the entry.
    :ivar str state:
        Represents if the Feature flag is On/Off/Conditionally On
    :ivar str description:
        Description of Feature Flag
    :ivar bool locked:
        Represents whether the feature flag is locked.
    :ivar datetime last_modified:
        A datetime object representing the last time the feature flag was modified.
    :ivar str etag:
        The ETag contains a value that you can use to perform operations.
    :ivar dict {string, FeatureFilter[]>} conditions:
        Dictionary that contains client_filters List (and server_filters List in future)
    '''

    def __init__(self, 
                key, 
                label=None, 
                state=None, 
                description=None,
                conditions=None,
                locked=None,
                last_modified=None):
        self.key = key
        self.label = label
        self.state = state.name.lower()
        self.description = description
        self.conditions = conditions
        self.last_modified = last_modified
        self.locked = locked

    def __str__(self):
        featureflagdisplay = {
            "Key": self.key,
            "Label": self.label,
            "State": self.state,
            "Locked": self.locked,
            "Description": self.description,
            "Last Modified": self.last_modified,
            "Conditions": custom_serialize_conditions(self.conditions)
        }

        return json.dumps(featureflagdisplay, indent=2)


class FeatureFilter(object):
    '''
    Feature filters class.
   
    :ivar str Name:
        Name of the filter
    :ivar dict {str, str} parameters:
        Name-Value pairs of parameters
    '''

    def __init__(self, 
                name, 
                parameters=None):
        self.name = name
        self.parameters = parameters

    def __repr__(self):
        featurefilter = {
            "name": self.name,
            "parameters": self.parameters
        }
        return json.dumps(featurefilter,indent=2)


# Helper Function to serialize Conditions
# Conditions will be dict {str, List[FeatureFilter]}
def custom_serialize_conditions(conditions_dict):
    featurefilterdict = {}
    if conditions_dict:
        for key,value in conditions_dict.items():
            featurefilters = []
            for filter in value:
                featurefilters.append(str(filter))
        featurefilterdict[key] = featurefilters
    return featurefilterdict


def map_keyvalue_to_featureflagdisplay(keyvalue, show_conditions=True):

    feature_flag_value = map_valuestr_to_valuedict(keyvalue)

    state = FeatureState.OFF
    if feature_flag_value.get('enabled', False):
        state = FeatureState.ON
    
    default_conditions = {}
    default_conditions['client_filters'] = []
    conditions = feature_flag_value.get('conditions', default_conditions)

    # if conditions["client_filters"] list is not empty, make state conditional
    # generalizing for conditions["server_filters"] in future
    for value in conditions.values():
        if value and state == FeatureState.ON:
            state = FeatureState.CONDITIONAL
            break

    # Key attribute not found should always raise error
    try:
        featurename = getattr(keyvalue, 'key')
        if featurename:
            feature_name = featurename[len(FEATURE_FLAG_PREFIX):]
    except AttributeError as exception:
        logger.error("Could not find 'key' attribute in the Key-Value data.")
        raise CLIError(str(exception))
    except Exception as exception:
        raise CLIError(str(exception))

    feature_flag_display = FeatureFlagDisplay(feature_name,
                                            getattr(keyvalue, 'label', ""),
                                            state,
                                            feature_flag_value.get('description', ""),
                                            conditions,
                                            getattr(keyvalue, 'locked', False),
                                            getattr(keyvalue, 'last_modified', ""))

    # By Default, we will try to show conditions unless the user has
    # specifically filtered them using --fields arg. 
    # But in some operations like 'Delete feature', we don't want 
    # to display all the conditions as a result of delete operation
    if not show_conditions:
        del feature_flag_display.conditions
    return feature_flag_display


def map_valuestr_to_valuedict(keyvalue):
    feature_flag_value = {}
    
    # Key attribute not found should always raise error
    try:
        featurename = getattr(keyvalue, 'key')
        if featurename:
            feature_name = featurename[len(FEATURE_FLAG_PREFIX):]
    except AttributeError as exception:
        logger.error("Could not find 'key' attribute in the Key-Value data.")
        raise CLIError(str(exception))
    except Exception as exception:
        raise CLIError(str(exception))
    
    valuestr = getattr(keyvalue, 'value', "")
    if valuestr:
        # Make sure value string is a valid json
        try:
            feature_flag_value = json.loads(valuestr)
        except ValueError as exception:
            logger.error("Unable to decode JSON value {}. \nFull Exception: \n{}".format(valuestr, str(exception)))
            raise CLIError("Feature flag {} contains invalid value.".format(feature_name))
        except Exception as exception:
            raise CLIError(str(exception))

    return feature_flag_value


def map_json_to_featurefilter(json_object):
    featurefilters = FeatureFilter( __get_value(json_object, 'name'),
                                    __get_value(json_object, 'parameters'))
    return featurefilters


def __get_value(item, argument):
    try:
        return item[argument]
    except (KeyError, TypeError, IndexError):
        return None

