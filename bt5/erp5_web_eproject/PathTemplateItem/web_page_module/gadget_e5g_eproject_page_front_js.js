/*global window, rJS, RSVP */
/*jslint nomen: true, indent: 2, maxerr: 3 */
(function (window, rJS, RSVP) {
  "use strict";

  /////////////////////////////////////////////////////////////////
  // api
  /////////////////////////////////////////////////////////////////

  // temporary options:
  //  gadget_href         [string]  url of gadget to load into a cell
  //  gadget_portal_link  [string]  portal type url (to avoid fetching)
  //  gadget_query        [object]  query parameters for data to display
  //  gadegt_title        [string]  title for listbox
  //  gadget_portal       [string]  portal type to link to

  var HARDCODED_GRID_LIST = [
    [{
      "gadget_href": "gadget_e5g_eproject_field_listbox_widget.html",
      "gadget_portal_link": "#jio_key=project_module&view=view",
      "gadget_title": "Projects",
      "gadget_portal": "Project",
      "gadget_query": {
        "query": 'portal_type: "Project"',
        "select_list": ["title"],
        "limit": [0, 5]
      }
    }, {
      "gadget_href": "gadget_e5g_eproject_field_listbox_widget.html",
      "gadget_portal_link": "#jio_key=task_module&view=view",
      "gadget_title": "Tasks",
      "gadget_portal": "Task",
      "gadget_query": {
        "query": 'portal_type: "Task"',
        "select_list": ["title"],
        "limit": [0, 5]
      }
    }]
  ];

  /////////////////////////////////////////////////////////////////
  // some methods
  /////////////////////////////////////////////////////////////////

  /////////////////////////////////////////////////////////////////
  // RJS
  /////////////////////////////////////////////////////////////////

  rJS(window)

    /////////////////////////////////////////////////////////////////
    // ready
    /////////////////////////////////////////////////////////////////
    .ready(function (my_gadget) {
      my_gadget.property_dict = {};
    })

    .ready(function (my_gadget) {
      return my_gadget.getElement()
        .push(function (my_element) {
          my_gadget.property_dict.element = my_element;
        });
    })

    /////////////////////////////////////////////////////////////////
    // published methods
    /////////////////////////////////////////////////////////////////

    /////////////////////////////////////////////////////////////////
    // acquired methods
    /////////////////////////////////////////////////////////////////

    /////////////////////////////////////////////////////////////////
    // published methods
    /////////////////////////////////////////////////////////////////

    /////////////////////////////////////////////////////////////////
    // declared methods
    /////////////////////////////////////////////////////////////////

    /////////////////////////////////////////////////////////////////
    // declared service
    /////////////////////////////////////////////////////////////////
    .declareMethod("render", function () {
      var gadget = this;
      return new RSVP.Queue()
        .push(function () {
          return gadget.declareGadget("gadget_erp5_grid.html", {
            "scope": "grid"
          });
        })
        .push(function () {
          return gadget.getDeclaredGadget("grid");
        })
        .push(function (my_grid_gadget) {
          return my_grid_gadget.render({"layout": HARDCODED_GRID_LIST});
        })
        .push(function (my_content_gadget) {
          gadget.property_dict.element.appendChild(
            my_content_gadget.property_dict.element
          );
        });
    });

}(window, rJS, RSVP));
