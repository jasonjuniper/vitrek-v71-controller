<?xml version='1.0'?>
<Project Name="Template - Generic.lvproj" Type="Project" LVVersion="8208000" URL="/&lt;instrlib&gt;/_Template - Generic/Template - Generic.lvproj">
   <Property Name="Instrument Driver" Type="Str">True</Property>
   <Property Name="NI.Project.Description" Type="Str">This project is used by developers to edit API and example files for LabVIEW Plug and Play instrument drivers.</Property>
   <Item Name="My Computer" Type="My Computer">
      <Property Name="CCSymbols" Type="Str">OS,Win;CPU,x86;</Property>
      <Property Name="specify.custom.address" Type="Bool">false</Property>
      <Item Name="VITREK V7X.lvlib" Type="Library" URL="VITREK V7X.lvlib">
         <Item Name="Public" Type="Folder">
            <Item Name="Action-Status" Type="Folder">
               <Item Name="Action-Status.mnu" Type="Document" URL="Public/Action-Status/Action-Status.mnu"/>
               <Item Name="ABORT.vi" Type="VI" URL="Public/Action-Status/ABORT.vi"/>
               <Item Name="LOCAL.vi" Type="VI" URL="Public/Action-Status/LOCAL.vi"/>
               <Item Name="HOLD.vi" Type="VI" URL="Public/Action-Status/HOLD.vi"/>
               <Item Name="LOCKOUT.vi" Type="VI" URL="Public/Action-Status/LOCKOUT.vi"/>
               <Item Name="NAMESEQ.vi" Type="VI" URL="Public/Action-Status/NAMESEQ.vi"/>
               <Item Name="NOSEQ.vi" Type="VI" URL="Public/Action-Status/NOSEQ.vi"/>
               <Item Name="PAUSE.vi" Type="VI" URL="Public/Action-Status/PAUSE.vi"/>
               <Item Name="RCLSEQ.vi" Type="VI" URL="Public/Action-Status/RCLSEQ.vi"/>
               <Item Name="Reset(RST).vi" Type="VI" URL="Public/Action-Status/Reset(RST).vi"/>
               <Item Name="RUN.vi" Type="VI" URL="Public/Action-Status/RUN.vi"/>
               <Item Name="SAVESEQ.vi" Type="VI" URL="Public/Action-Status/SAVESEQ.vi"/>
               <Item Name="SEQ Status.vi" Type="VI" URL="Public/Action-Status/SEQ Status.vi"/>
               <Item Name="STEPFLAG.vi" Type="VI" URL="Public/Action-Status/STEPFLAG.vi"/>
               <Item Name="STEPSTATE.vi" Type="VI" URL="Public/Action-Status/STEPSTATE.vi"/>
            </Item>
            <Item Name="Application" Type="Folder">
               <Item Name="Application.mnu" Type="Document" URL="Public/Application/Application.mnu"/>
               <Item Name="AddADCWSEQ.vi" Type="VI" URL="Public/Application/AddADCWSEQ.vi"/>
               <Item Name="AddCONTSEQ.vi" Type="VI" URL="Public/Application/AddCONTSEQ.vi"/>
               <Item Name="AddGBSEQ.vi" Type="VI" URL="Public/Application/AddGBSEQ.vi"/>
               <Item Name="AddIRSEQ.vi" Type="VI" URL="Public/Application/AddIRSEQ.vi"/>
               <Item Name="RunSEQ.vi" Type="VI" URL="Public/Application/RunSEQ.vi"/>
            </Item>
            <Item Name="Configure" Type="Folder">
               <Item Name="Utility" Type="Folder">
                  <Item Name="Utility.mnu" Type="Document" URL="Public/Configure/Utility/Utility.mnu"/>
                  <Item Name="Configure ARC.vi" Type="VI" URL="Public/Configure/Utility/Configure ARC.vi"/>
                  <Item Name="Configure BEEP.vi" Type="VI" URL="Public/Configure/Utility/Configure BEEP.vi"/>
                  <Item Name="Configure CONTFAIL.vi" Type="VI" URL="Public/Configure/Utility/Configure CONTFAIL.vi"/>
                  <Item Name="Configure DIO.vi" Type="VI" URL="Public/Configure/Utility/Configure DIO.vi"/>
                  <Item Name="Configure FREQ.vi" Type="VI" URL="Public/Configure/Utility/Configure FREQ.vi"/>
                  <Item Name="Configure IREND.vi" Type="VI" URL="Public/Configure/Utility/Configure IREND.vi"/>
                  <Item Name="Configure RAMPDOWN.vi" Type="VI" URL="Public/Configure/Utility/Configure RAMPDOWN.vi"/>
                  <Item Name="Configure START.vi" Type="VI" URL="Public/Configure/Utility/Configure START.vi"/>
               </Item>
               <Item Name="Configure.mnu" Type="Document" URL="Public/Configure/Configure.mnu"/>
               <Item Name="Configure ADCW.vi" Type="VI" URL="Public/Configure/Configure ADCW.vi"/>
               <Item Name="Configure CONT.vi" Type="VI" URL="Public/Configure/Configure CONT.vi"/>
               <Item Name="Configure GB.vi" Type="VI" URL="Public/Configure/Configure GB.vi"/>
               <Item Name="Configure IR.vi" Type="VI" URL="Public/Configure/Configure IR.vi"/>
            </Item>
            <Item Name="Data" Type="Folder">
               <Item Name="Data.mnu" Type="Document" URL="Public/Data/Data.mnu"/>
               <Item Name="MEASRSLT.vi" Type="VI" URL="Public/Data/MEASRSLT.vi"/>
               <Item Name="STEPRSLT.vi" Type="VI" URL="Public/Data/STEPRSLT.vi"/>
            </Item>
            <Item Name="Utility" Type="Folder">
               <Item Name="Utility.mnu" Type="Document" URL="Public/Utility/Utility.mnu"/>
               <Item Name="Error Query.vi" Type="VI" URL="Public/Utility/Error Query.vi">
                  <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
               </Item>
               <Item Name="Reset.vi" Type="VI" URL="Public/Utility/Reset.vi"/>
               <Item Name="Revision Query.vi" Type="VI" URL="Public/Utility/Revision Query.vi">
                  <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
               </Item>
               <Item Name="Self-Test.vi" Type="VI" URL="Public/Utility/Self-Test.vi">
                  <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
               </Item>
            </Item>
            <Item Name="dir.mnu" Type="Document" URL="Public/dir.mnu"/>
            <Item Name="Close.vi" Type="VI" URL="Public/Close.vi">
               <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
            </Item>
            <Item Name="Initialize.vi" Type="VI" URL="Public/Initialize.vi">
               <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
            </Item>
            <Item Name="VI Tree.vi" Type="VI" URL="Public/VI Tree.vi">
               <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
            </Item>
         </Item>
         <Item Name="Private" Type="Folder">
            <Item Name="Default Instrument Setup.vi" Type="VI" URL="Private/Default Instrument Setup.vi">
               <Property Name="NI.Lib.ShowInTree" Type="Bool">true</Property>
            </Item>
         </Item>
         <Item Name="VITREK V7X Readme.html" Type="Document" URL="VITREK V7X Readme.html"/>
      </Item>
      <Item Name="Dependencies" Type="Dependencies"/>
      <Item Name="Build Specifications" Type="Build"/>
   </Item>
</Project>
