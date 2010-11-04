# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (c) 2009 Nexedi SA and Contributors. All Rights Reserved.
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsibility of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# guarantees and support are strongly adviced to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
##############################################################################

import zope.interface
from AccessControl import ClassSecurityInfo
from Products.ERP5Type import Permissions, interfaces
from Products.ERP5.MovementCollectionDiff import MovementCollectionDiff
from Products.ERP5.mixin.rule import _compare

class MovementCollectionUpdaterMixin:
  """Movement Collection Updater interface specification

  Documents which implement IMovementCollectionUpdater
  usually invoke an IMovementGenerator to generate
  an IMovementList and compare it to another IMovementList
  obtained from an IMovementCollection, thus generating
  an IMovementCollectionDiff.
  """

  # Declarative security
  security = ClassSecurityInfo()
  security.declareObjectProtected(Permissions.AccessContentsInformation)

  # Declarative interfaces
  zope.interface.implements(interfaces.IMovementCollectionUpdater,)

  # Implementation of IMovementCollectionUpdater
  def getMovementCollectionDiff(self, context, rounding=False,
                                movement_generator=None):
    """
    Return a IMovementCollectionDiff by comparing movements
    the list of movements of context and the list of movements
    generated by movement_generator on context.

    context -- an IMovementCollection usually, possibly
               an IMovementList or an IMovement

    movement_generator -- an optional IMovementGenerator
                          (if not specified, a context implicit
                          IMovementGenerator will be used)
    """
    # We suppose here that we have an IMovementCollection in hand
    decision_movement_list = context.getMovementList()
    prevision_movement_list = movement_generator.getGeneratedMovementList(
      movement_list=self._getMovementGeneratorMovementList(context), rounding=rounding) # XXX-JPS This mixin is not self-contained

    # Get divergence testers
    tester_list = self._getMatchingTesterList()
    if not tester_list and len(prevision_movement_list) > 1:
      raise ValueError("It is not possible to match movements without divergence testers")

    # Create small groups of movements per hash keys
    decision_movement_dict = {}
    for movement in decision_movement_list:
      tester_key = []
      for tester in tester_list:
        if tester.test(movement):
          tester_key.append(tester.generateHashKey(movement))
        else:
          tester_key.append(None)
      tester_key = tuple(tester_key)
      decision_movement_dict.setdefault(tester_key, []).append(movement)
    prevision_movement_dict = {}
    for movement in prevision_movement_list:
      tester_key = []
      for tester in tester_list:
        if tester.test(movement):
          tester_key.append(tester.generateHashKey(movement))
        else:
          tester_key.append(None)
      tester_key = tuple(tester_key)
      prevision_movement_dict.setdefault(tester_key, []).append(movement)

    # Prepare a mapping between prevision and decision
    # The prevision_to_decision_map is a list of tuples
    # of the form (prevision_movement_dict, list of decision_movement)
    prevision_to_decision_map = []

    # First find out all existing (decision) movements which belong to no group
    no_group_list = []
    for tester_key in decision_movement_dict.keys():
      if prevision_movement_dict.has_key(tester_key):
        for decision_movement in decision_movement_dict[tester_key]:
          no_match = True
          for prevision_movement in prevision_movement_dict[tester_key]:
            # Check if this movement belongs to an existing group
            if _compare(tester_list, prevision_movement, decision_movement):
              no_match = False
              break
          if no_match:
            # There is no matching.
            # So, let us add the decision movements to no_group_list
            no_group_list.append(decision_movement)
      else:
        # The tester key does not even exist.
        # So, let us add all decision movements to no_group_list
        no_group_list.extend(decision_movement_dict[tester_key])
    if len(no_group_list) > 0:
      prevision_to_decision_map.append((None, no_group_list))

    # Second, let us create small groups of movements
    for tester_key in prevision_movement_dict.keys():
      for prevision_movement in prevision_movement_dict[tester_key]:
        map_list = []
        for decision_movement in decision_movement_dict.get(tester_key, ()):
          if _compare(tester_list, prevision_movement, decision_movement):
            # XXX is it OK to have more than 2 decision_movements? # XXX-JPS - I think yes
            map_list.append(decision_movement)
        prevision_to_decision_map.append((prevision_movement, map_list))

    # Third, time to create the diff
    movement_collection_diff = MovementCollectionDiff()
    for (prevision_movement, decision_movement_list) in prevision_to_decision_map:
      self._extendMovementCollectionDiff(movement_collection_diff, prevision_movement,
                                         decision_movement_list)

    # Return result
    return movement_collection_diff

  def updateMovementCollection(self, context, rounding=False,
                               movement_generator=None):
    """
    Invoke getMovementCollectionDiff and update context with
    the resulting IMovementCollectionDiff.

    context -- an IMovementCollection usually, possibly
               an IMovementList or an IMovement

    movement_generator -- an optional IMovementGenerator
                          (if not specified, a context implicit
                          IMovementGenerator will be used)
    """
    movement_diff = self.getMovementCollectionDiff(context,
                 rounding=rounding, movement_generator=movement_generator)

    # Apply Diff
    for movement in movement_diff.getDeletableMovementList():
      movement.getParentValue().deleteContent(movement.getId())
    for movement in movement_diff.getUpdatableMovementList():
      kw = movement_diff.getMovementPropertyDict(movement)
      movement.edit(**kw)
      for property_id in kw.iterkeys():
        movement.clearRecordedProperty(property_id)
    for movement in movement_diff.getNewMovementList():
      # This case is easy, because it is an applied rule
      kw = movement_diff.getMovementPropertyDict(movement)
      movement = context.newContent(portal_type=self.movement_type, **kw)
