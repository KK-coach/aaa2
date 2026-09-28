# locked_ngx_popover_overview

- URL: https://valor-software.com/ngx-bootstrap/components/popover?tab=overview
- nyelv: en
- site: This page belongs to the website valor-software.com, whose home page is titled “Angular Bootstrap”.
- blokkok (content régió): 209

Blokkonként: azonosító, típus, heading-útvonal, szöveg (táblázatsornál a cellák az oszlopfejléccel).

- **b0** `title`: Angular Bootstrap
- **b74** `list_item`: Home
- **b75** `list_item`: / components
- **b76** `list_item`: / popover
- **b77** `heading`: Popover
- **b78** `paragraph` (Popover): Add small overlay content, like those found in iOS, to any element for housing secondary information.
- **b79** `paragraph` (Popover): The easiest way to add the popover component to your app (will be added to the root module)
- **b80** `list_item` (Popover): Overview
- **b81** `list_item` (Popover): API
- **b82** `list_item` (Popover): Examples
- **b83** `heading`: Basic
- **b84** `paragraph` (Popover › Basic): #
- **b85** `other` (Popover › Basic): Live demo
- **b86** `list_item` (Popover › Basic): template
- **b87** `list_item` (Popover › Basic): component
- **b88** `code` (Popover › Basic)

  ```
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" >
  Live demo
  </button>
  ```

- **b89** `code` (Popover › Basic)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-basic' ,
  templateUrl : './basic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverBasicComponent {}
  ```

- **b90** `heading`: Placement
- **b91** `paragraph` (Popover › Placement): #
- **b92** `paragraph` (Popover › Placement): Four base positioning options are available: top , right , bottom , and left . Besides that, auto option may be used to detect a position that fits the component on screen.
- **b93** `other` (Popover › Placement): Popover on top
- **b94** `other` (Popover › Placement): Popover on right
- **b95** `other` (Popover › Placement): Popover auto
- **b96** `other` (Popover › Placement): Popover on left
- **b97** `other` (Popover › Placement): Popover on bottom
- **b98** `list_item` (Popover › Placement): template
- **b99** `list_item` (Popover › Placement): component
- **b100** `code` (Popover › Placement)

  ```
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on top"
  placement = "top" >
  Popover on top
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on right"
  placement = "right" >
  Popover on right
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover auto"
  placement = "auto" >
  Popover auto
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on left"
  placement = "left" >
  Popover on left
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on bottom"
  placement = "bottom" >
  Popover on bottom
  </button>
  ```

- **b101** `code` (Popover › Placement)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-placement' ,
  templateUrl : './placement.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverPlacementComponent {}
  ```

- **b102** `heading`: Corner placement
- **b103** `paragraph` (Popover › Corner placement): #
- **b104** `paragraph` (Popover › Corner placement): Placement property of a popover can contain "corner placement" specifier following the base positioning. Thus, in addition to the four base positioning options, namely top , right , bottom , and left , eight more positioning options are available: top left , top right , right top , right bottom , bottom right , bottom left , left bottom , and left top .
- **b105** `paragraph` (Popover › Corner placement): top left top right right top right bottom bottom right bottom left left bottom left top
- **b106** `other` (Popover › Corner placement): Popover on top left
- **b107** `list_item` (Popover › Corner placement): template
- **b108** `list_item` (Popover › Corner placement): component
- **b109** `code` (Popover › Corner placement)

  ```
  <p>
  <select [( ngModel )] = "placement" class = "form-control" >
  @for (placement of placements; track placement) {
  <option
  [ value ] = "placement" >
  {{ placement }}
  </option>
  }
  </select>
  </p>
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  [ popoverTitle ] = "'Popover on ' + placement"
  [ placement ] = "placement" >
  {{ 'Popover on ' + placement }}
  </button>
  ```

- **b110** `code` (Popover › Corner placement)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-corner-placement' ,
  templateUrl : './corner-placement.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverCornerPlacementComponent {
  placements = [
  'top left' ,
  'top right' ,
  'right top' ,
  'right bottom' ,
  'bottom right' ,
  'bottom left' ,
  'left bottom' ,
  'left top'
  ];
  placement : "top" | "bottom" | "left" | "right" | "auto" | "top left" | "top right" | "right top" | "right bottom" | "bottom right" | "bottom left" | "left bottom" | "left top" = 'top left' ;
  }
  ```

- **b111** `heading`: Disable adaptive position
- **b112** `paragraph` (Popover › Disable adaptive position): #
- **b113** `paragraph` (Popover › Disable adaptive position): You can disable adaptive position via adaptivePosition input or config option
- **b114** `other` (Popover › Disable adaptive position): Popover on top
- **b115** `other` (Popover › Disable adaptive position): Popover on right
- **b116** `list_item` (Popover › Disable adaptive position): template
- **b117** `list_item` (Popover › Disable adaptive position): component
- **b118** `code` (Popover › Disable adaptive position)

  ```
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on top"
  [ adaptivePosition ] = "false"
  placement = "top" >
  Popover on top
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Popover on right"
  [ adaptivePosition ] = "false"
  placement = "right" >
  Popover on right
  </button>
  ```

- **b119** `code` (Popover › Disable adaptive position)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-adaptive-position' ,
  templateUrl : './adaptive-position.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverAdaptivePositionComponent {}
  ```

- **b120** `heading`: Adaptive position with overflow boundary
- **b121** `paragraph` (Popover › Adaptive position with overflow boundary): #
- **b122** `paragraph` (Popover › Adaptive position with overflow boundary): You can control the popover boundaries via boundariesElement input or config option. boundariesElement property of a popover can contain boundaries namely viewport, scrollParent, window .
- **b123** `other` (Popover › Adaptive position with overflow boundary): Popover on top
- **b124** `other` (Popover › Adaptive position with overflow boundary): Popover on bottom
- **b125** `list_item` (Popover › Adaptive position with overflow boundary): template
- **b126** `list_item` (Popover › Adaptive position with overflow boundary): component
- **b127** `code` (Popover › Adaptive position with overflow boundary)

  ```
  <div class = "container" >
  
  <div class = "btn-padding" >
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" popoverTitle = "Popover on top"
  [ adaptivePosition ] = "true" container = "body" boundariesElement = "viewport" placement = "top" >
  Popover on top
  </button>
  
  <button type = "button" class = "btn btn-default btn-secondary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" popoverTitle = "Popover on bottom"
  [ adaptivePosition ] = "true" container = "body" boundariesElement = "viewport" placement = "bottom" >
  Popover on bottom
  </button>
  </div>
  
  </div>
  ```

- **b128** `code` (Popover › Adaptive position with overflow boundary)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-adaptive-position-overflow-boundary' ,
  templateUrl : './adaptive-position-overflow-boundary.html' ,
  styleUrls : [ './adaptive-position-overflow-boundary.css' ],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoAdaptivePositionOverflowBoundaryComponent {}
  ```

- **b129** `heading`: Dismiss on next click
- **b130** `paragraph` (Popover › Dismiss on next click): #
- **b131** `paragraph` (Popover › Dismiss on next click): Use the focus trigger to dismiss popovers on the next click that the user makes.
- **b132** `other` (Popover › Dismiss on next click): Dismissible popover
- **b133** `list_item` (Popover › Dismiss on next click): template
- **b134** `list_item` (Popover › Dismiss on next click): component
- **b135** `code` (Popover › Dismiss on next click)

  ```
  <button type = "button" class = "btn btn-success"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  popoverTitle = "Dismissible popover"
  triggers = "focus" >
  Dismissible popover
  </button>
  ```

- **b136** `code` (Popover › Dismiss on next click)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-dismiss' ,
  templateUrl : './dismiss.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverDismissComponent {}
  ```

- **b137** `heading`: Dynamic content
- **b138** `paragraph` (Popover › Dynamic content): #
- **b139** `paragraph` (Popover › Dynamic content): Pass a string as popover content.
- **b140** `other` (Popover › Dynamic content): Simple binding
- **b141** `list_item` (Popover › Dynamic content): template
- **b142** `list_item` (Popover › Dynamic content): component
- **b143** `code` (Popover › Dynamic content)

  ```
  <button type = "button" class = "btn btn-info"
  [ popover ] = "content" [ popoverTitle ] = "title" >
  Simple binding
  </button>
  ```

- **b144** `code` (Popover › Dynamic content)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-dynamic' ,
  templateUrl : './dynamic.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverDynamicComponent {
  title = 'Welcome word' ;
  content = 'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.' ;
  }
  ```

- **b145** `heading`: Custom content template
- **b146** `paragraph` (Popover › Custom content template): #
- **b147** `paragraph` (Popover › Custom content template): Create <ng-template #myId> with any html allowed by Angular, and provide template ref [popover]="myId" as popover content.
- **b148** `other` (Popover › Custom content template): TemplateRef binding
- **b149** `list_item` (Popover › Custom content template): template
- **b150** `list_item` (Popover › Custom content template): component
- **b151** `code` (Popover › Custom content template)

  ```
  <ng-template # popTemplate > Just another: {{content}} </ng-template>
  <button type = "button" class = "btn btn-warning"
  [ popover ] = "popTemplate" popoverTitle = "Template ref content inside" >
  TemplateRef binding
  </button>
  ```

- **b152** `code` (Popover › Custom content template)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-custom-content' ,
  templateUrl : './custom-content.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverCustomContentComponent {
  title = 'Welcome word' ;
  content = 'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.' ;
  }
  ```

- **b153** `heading`: Dynamic Html
- **b154** `paragraph` (Popover › Dynamic Html): #
- **b155** `paragraph` (Popover › Dynamic Html): By using [innerHtml] inside ng-template you can display any dynamic html
- **b156** `other` (Popover › Dynamic Html): Show me popover with html
- **b157** `list_item` (Popover › Dynamic Html): template
- **b158** `list_item` (Popover › Dynamic Html): component
- **b159** `code` (Popover › Dynamic Html)

  ```
  <ng-template # popTemplate > Here we go: <div [ innerHtml ] = "html" ></div></ng-template>
  <button type = "button" class = "btn btn-success"
  [ popover ] = "popTemplate" popoverTitle = "Dynamic html inside" >
  Show me popover with html
  </button>
  ```

- **b160** `code` (Popover › Dynamic Html)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-dynamic-html' ,
  templateUrl : './dynamic-html.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverDynamicHtmlComponent {
  html = `< span class = "btn btn-danger" > Never trust not sanitized HTML !!!</ span >`;
  }
  ```

- **b161** `heading`: Append to body
- **b162** `paragraph` (Popover › Append to body): #
- **b163** `paragraph` (Popover › Append to body): When you have any styles on a parent element that interfere with a popover, you’ll want to specify a container="body" so that the popover’s HTML will be appended to body. This will help to avoid rendering problems in more complex components (like input groups, button groups, etc) or inside elements with overflow: hidden
- **b164** `other` (Popover › Append to body): Default popover
- **b165** `other` (Popover › Append to body): Popover appended to body
- **b166** `list_item` (Popover › Append to body): template
- **b167** `list_item` (Popover › Append to body): component
- **b168** `code` (Popover › Append to body)

  ```
  <div class = "row panel" style = " position : relative ; overflow : hidden ; " >
  <div class = "card-block panel-body" >
  <button type = "button" class = "btn btn-danger"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" >
  Default popover
  </button>
  <button type = "button" class = "btn btn-success"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  container = "body" >
  Popover appended to body
  </button>
  </div>
  </div>
  ```

- **b169** `code` (Popover › Append to body)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-container' ,
  templateUrl : './container.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverContainerComponent {}
  ```

- **b170** `heading`: Visibility events
- **b171** `paragraph` (Popover › Visibility events): #
- **b172** `other` (Popover › Visibility events): Live demo
- **b173** `code` (Popover › Visibility events)

  ```
  Event:
  ```

- **b174** `list_item` (Popover › Visibility events): template
- **b175** `list_item` (Popover › Visibility events): component
- **b176** `code` (Popover › Visibility events)

  ```
  <button type = "button" class = "btn btn-primary"
  ( onShown ) = "onShown()" ( onHidden ) = "onHidden()"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" >
  Live demo
  </button>
  <br>
  <br>
  <pre class = "card card-block card-header mb-3" > Event: {{message}} </pre>
  ```

- **b177** `code` (Popover › Visibility events)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-events' ,
  templateUrl : './events.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverEventsComponent {
  message ?: string ;
  
  onShown (): void {
  this . message = 'shown' ;
  }
  
  onHidden (): void {
  this . message = 'hidden' ;
  }
  }
  ```

- **b178** `heading`: Configuring defaults
- **b179** `paragraph` (Popover › Configuring defaults): #
- **b180** `other` (Popover › Configuring defaults): Preconfigured popover
- **b181** `list_item` (Popover › Configuring defaults): template
- **b182** `list_item` (Popover › Configuring defaults): component
- **b183** `code` (Popover › Configuring defaults)

  ```
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" >
  Preconfigured popover
  </button>
  ```

- **b184** `code` (Popover › Configuring defaults)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  import { PopoverConfig } from 'ngx-bootstrap/popover' ;
  
  // such override allows to keep some initial values
  
  export function getPopoverConfig (): PopoverConfig {
  return Object . assign ( new PopoverConfig (), {
  placement : 'right' ,
  container : 'body' ,
  triggers : 'focus' ,
  delay : 500
  });
  }
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-config' ,
  templateUrl : './config.html' ,
  providers : [{ provide : PopoverConfig , useFactory : getPopoverConfig }],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverConfigComponent {}
  ```

- **b185** `heading`: Outside click
- **b186** `paragraph` (Popover › Outside click): #
- **b187** `other` (Popover › Outside click): Live demo
- **b188** `list_item` (Popover › Outside click): template
- **b189** `list_item` (Popover › Outside click): component
- **b190** `code` (Popover › Outside click)

  ```
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'"
  [ outsideClick ] = "true" >
  Live demo
  </button>
  ```

- **b191** `code` (Popover › Outside click)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-outside-click' ,
  templateUrl : './outside-click.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverOutsideClickComponent {}
  ```

- **b192** `heading`: Custom triggers
- **b193** `paragraph` (Popover › Custom triggers): #
- **b194** `other` (Popover › Custom triggers): Hover over me!
- **b195** `other` (Popover › Custom triggers): Double click me!
- **b196** `list_item` (Popover › Custom triggers): template
- **b197** `list_item` (Popover › Custom triggers): component
- **b198** `code` (Popover › Custom triggers)

  ```
  <div class = "row" >
  <div class = "col-md-2" >
  <button type = "button" class = "btn btn-info"
  [ popover ] = "'I will hide on blur'"
  triggers = "mouseenter:mouseleave" >
  Hover over me!
  </button>
  </div>
  <div class = "col-md-2" >
  <button type = "button" class = "btn btn-info"
  [ popover ] = "'Double click one more time'"
  triggers = "dblclick" >
  Double click me!
  </button>
  </div>
  <div class = "col-md-3" >
  <input type = "text"
  class = "form-control"
  placeholder = "Show popover on input change"
  [ popover ] = "'I will hide on blur'"
  triggers = "keypress:focusout" >
  </div>
  </div>
  ```

- **b199** `code` (Popover › Custom triggers)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-triggers-custom' ,
  templateUrl : './triggers-custom.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverTriggersCustomComponent {}
  ```

- **b200** `heading`: Manual triggering
- **b201** `paragraph` (Popover › Manual triggering): #
- **b202** `paragraph` (Popover › Manual triggering): This demo shows manipulating popover by show , hide and toggle methods
- **b203** `paragraph` (Popover › Manual triggering): This text has attached popover
- **b204** `other` (Popover › Manual triggering): Show Hide Toggle
- **b205** `list_item` (Popover › Manual triggering): template
- **b206** `list_item` (Popover › Manual triggering): component
- **b207** `code` (Popover › Manual triggering)

  ```
  <p>
  <span [ popover ] = "'Hello there! I was triggered manually'"
  triggers = "" # pop = "bs-popover" >
  This text has attached popover
  </span>
  </p>
  
  <button type = "button" class = "btn btn-success" ( click ) = "pop.show()" >
  Show
  </button>
  <button type = "button" class = "btn btn-warning" ( click ) = "pop.hide()" >
  Hide
  </button>
  <button type = "button" class = "btn btn-info" ( click ) = "pop.toggle()" >
  Toggle
  </button>
  ```

- **b208** `code` (Popover › Manual triggering)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-triggers-manual' ,
  templateUrl : './triggers-manual.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverTriggersManualComponent {}
  ```

- **b209** `heading`: Trigger by isOpen property
- **b210** `paragraph` (Popover › Trigger by isOpen property): #
- **b211** `paragraph` (Popover › Trigger by isOpen property): You can show/hide popover by switching isOpen property
- **b212** `paragraph` (Popover › Trigger by isOpen property): This text has attached popover
- **b213** `other` (Popover › Trigger by isOpen property): Toggle
- **b214** `list_item` (Popover › Trigger by isOpen property): template
- **b215** `list_item` (Popover › Trigger by isOpen property): component
- **b216** `code` (Popover › Trigger by isOpen property)

  ```
  <p>
  <span [ popover ] = "'Hello there! I was triggered by changing isOpen property'"
  triggers = "" [( isOpen )] = "isOpen" >
  This text has attached popover
  </span>
  </p>
  <button type = "button" class = "btn btn-primary"
  ( click ) = "isOpen = !isOpen" >
  Toggle
  </button>
  ```

- **b217** `code` (Popover › Trigger by isOpen property)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-trigger-by-isopen' ,
  templateUrl : './trigger-by-isopen-property.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverByIsOpenPropComponent {
  isOpen = false ;
  }
  ```

- **b218** `heading`: Component level styling
- **b219** `paragraph` (Popover › Component level styling): #
- **b220** `other` (Popover › Component level styling): I have component level styling
- **b221** `list_item` (Popover › Component level styling): template
- **b222** `list_item` (Popover › Component level styling): component
- **b223** `code` (Popover › Component level styling)

  ```
  <button type = "button" class = "btn btn-info"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" >
  I have component level styling
  </button>
  ```

- **b224** `code` (Popover › Component level styling)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-styling-local' ,
  templateUrl : './styling-local.html' ,
  styles : [
  `
  : host . popover {
  background - color : # 009688 ;
  color : # fff ;
  }
  : host . popover >. arrow : after {
  border - top - color : # 009688 ;
  }
  `
  ],
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverStylingLocalComponent {}
  ```

- **b225** `heading`: Custom class
- **b226** `paragraph` (Popover › Custom class): #
- **b227** `other` (Popover › Custom class): Custom class demo
- **b228** `list_item` (Popover › Custom class): template
- **b229** `list_item` (Popover › Custom class): component
- **b230** `code` (Popover › Custom class)

  ```
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" containerClass = "customClass" >
  Custom class demo
  </button>
  ```

- **b231** `code` (Popover › Custom class)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-class' ,
  templateUrl : './class.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverClassComponent {}
  ```

- **b232** `heading`: Popover context
- **b233** `paragraph` (Popover › Popover context): #
- **b234** `other` (Popover › Popover context): Open popover with custom context
- **b235** `list_item` (Popover › Popover context): template
- **b236** `list_item` (Popover › Popover context): component
- **b237** `code` (Popover › Popover context)

  ```
  <ng-template # popTemplate let-message = "message" > {{ message }} </ng-template>
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "popTemplate" [ popoverContext ] = "context" >
  Open popover with custom context
  </button>
  ```

- **b238** `code` (Popover › Popover context)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-context' ,
  templateUrl : './popover-context.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverContextComponent {
  context = {
  message : 'Hello there!'
  };
  }
  ```

- **b239** `heading`: Popover with delay
- **b240** `paragraph` (Popover › Popover with delay): #
- **b241** `paragraph` (Popover › Popover with delay): Click on the button to see popover delayed for 0,5 second
- **b242** `other` (Popover › Popover with delay): Popover with 0.5sec delay
- **b243** `list_item` (Popover › Popover with delay): template
- **b244** `list_item` (Popover › Popover with delay): component
- **b245** `code` (Popover › Popover with delay)

  ```
  <button type = "button" class = "btn btn-primary"
  [ popover ] = "'Vivamus sagittis lacus vel augue laoreet rutrum faucibus.'" [ delay ] = "500" >
  Popover with 0.5sec delay
  </button>
  ```

- **b246** `code` (Popover › Popover with delay)

  ```
  import { Component , ChangeDetectionStrategy } from '@angular/core' ;
  
  @Component ({
  // eslint-disable-next-line @angular-eslint/component-selector
  selector : 'demo-popover-delay' ,
  templateUrl : './delay.html' ,
  changeDetection : ChangeDetectionStrategy . Eager ,
  standalone : false
  })
  export class DemoPopoverDelayComponent {}
  ```

- **b247** `heading`: Installation
- **b248** `code` (Popover › Installation)

  ```
  ng add ngx-bootstrap  --component popover
  ```

- **b249** `code` (Popover › Installation)

  ```
  ### Standalone component usage
  import { PopoverModule } from 'ngx-bootstrap/popover';
  
  @Component({
    standalone: true,
    imports: [PopoverModule,...]
  })
  export class AppComponent(){}
  
  ### Module usage
  import { PopoverModule } from 'ngx-bootstrap/popover';
  
  @NgModule({
    imports: [PopoverModule,...]
  })
  export class AppModule(){}
  ```

- **b250** `heading`: PopoverDirective
- **b251** `paragraph` (Popover › Installation › PopoverDirective): A lightweight, extensible directive for fancy popover creation.
- **b252** `table_row` (Popover › Installation › PopoverDirective): Selector
- **b253** `heading`: PopoverConfig
- **b254** `paragraph` (Popover › Installation › PopoverConfig): Configuration service for the Popover directive. You can inject this service, typically in your root component, and customize the values of its properties in order to provide default values for all the popovers used in the application.
- **b255** `heading`: Properties
- **b256** `table_row` (Popover › Installation › Properties): adaptivePosition; Type: boolean Default value: true sets disable adaptive position
- **b257** `table_row` (Popover › Installation › Properties): container; Type: string | undefined A selector specifying the element the popover should be appended to.
- **b258** `table_row` (Popover › Installation › Properties): delay; Type: number Default value: 0 delay before showing the tooltip
- **b259** `table_row` (Popover › Installation › Properties): placement; Type: string Default value: top Placement of a popover. Accepts: "top", "bottom", "left", "right", "auto"
- **b260** `table_row` (Popover › Installation › Properties): triggers; Type: string Default value: click Specifies events that should trigger. Supports a space separated list of event names.
- **b261** `other` (Popover › Installation › Properties): components
- **b262** `list_item` (Popover › Installation › Properties): Basic
- **b263** `list_item` (Popover › Installation › Properties): Placement
- **b264** `list_item` (Popover › Installation › Properties): Corner placement
- **b265** `list_item` (Popover › Installation › Properties): Disable adaptive position
- **b266** `list_item` (Popover › Installation › Properties): Adaptive position with overflow boundary
- **b267** `list_item` (Popover › Installation › Properties): Dismiss on next click
- **b268** `list_item` (Popover › Installation › Properties): Dynamic content
- **b269** `list_item` (Popover › Installation › Properties): Custom content template
- **b270** `list_item` (Popover › Installation › Properties): Dynamic Html
- **b271** `list_item` (Popover › Installation › Properties): Append to body
- **b272** `list_item` (Popover › Installation › Properties): Visibility events
- **b273** `list_item` (Popover › Installation › Properties): Configuring defaults
- **b274** `list_item` (Popover › Installation › Properties): Outside click
- **b275** `list_item` (Popover › Installation › Properties): Custom triggers
- **b276** `list_item` (Popover › Installation › Properties): Manual triggering
- **b277** `list_item` (Popover › Installation › Properties): Trigger by isOpen property
- **b278** `list_item` (Popover › Installation › Properties): Component level styling
- **b279** `list_item` (Popover › Installation › Properties): Custom class
- **b280** `list_item` (Popover › Installation › Properties): Popover context
- **b281** `list_item` (Popover › Installation › Properties): Popover with delay
